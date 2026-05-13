using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using TMPro;
using UnityEngine;

namespace SmartRoom.Validation
{
    public class ValidationRunner : MonoBehaviour
    {
        [Header("Module References")]
        [SerializeField] private SceneGeometryCollector sceneCollector;
        [SerializeField] private SpatialAnchorPersistence anchorPersistence;

        [Header("HUD")]
        [SerializeField] private TMP_Text statusText;
        [SerializeField] private TMP_Text instructionText;

        [Header("Export")]
        [SerializeField] private bool autoExportOnCollect = true;
        [SerializeField] private string exportJsonFileName = "room_bounding_boxes.json";
        [SerializeField] private string exportCsvFileName = "room_bounding_boxes.csv";
        [SerializeField] private string anchorLogJsonFileName = "anchor_log.json";
        [SerializeField] private string anchorLogCsvFileName = "anchor_log.csv";

        [Header("V5 Test Keys")]
        [SerializeField] private KeyCode startV5TestKey = KeyCode.F7;
        [SerializeField] private int v5TestSessionsToRun = 3;
        [SerializeField] private float v5TestIntervalSeconds = 2f;

        private readonly List<AnchorLogEntry> _anchorLog = new List<AnchorLogEntry>();
        private int _v5TestSession;
        private bool _v5TestRunning;
        private string _lastExportDirectory;

        private void Awake()
        {
            if (sceneCollector == null)
            {
                sceneCollector = FindFirstObjectByType<SceneGeometryCollector>();
            }

            if (anchorPersistence == null)
            {
                anchorPersistence = FindFirstObjectByType<SpatialAnchorPersistence>();
            }
        }

        private void Start()
        {
            if (sceneCollector != null)
            {
                sceneCollector.OnGeometryCollected += HandleGeometryCollected;
                sceneCollector.OnCollectionFailed += HandleCollectionFailed;
            }

            if (anchorPersistence != null)
            {
                anchorPersistence.OnAnchorEvent += HandleAnchorEvent;
                anchorPersistence.OnLocalizationComplete += HandleLocalizationComplete;
            }

            UpdateHud("Ready. F4=Collect Geometry | F5=Create Anchor | F6=Query Anchors | F7=V5 Test | F8=Erase All");
        }

        private void Update()
        {
            if (Input.GetKeyDown(startV5TestKey) && !_v5TestRunning)
            {
                StartV5Test();
            }
        }

        public void StartV5Test()
        {
            if (anchorPersistence == null)
            {
                UpdateHud("V5 test cannot start: SpatialAnchorPersistence not assigned.");
                return;
            }

            _v5TestRunning = true;
            _v5TestSession = 0;
            _anchorLog.Clear();
            UpdateHud($"V5 Test starting... ({v5TestSessionsToRun} sessions)");
            StartCoroutine(RunV5TestCoroutine());
        }

        private System.Collections.IEnumerator RunV5TestCoroutine()
        {
            for (int i = 0; i < v5TestSessionsToRun; i++)
            {
                _v5TestSession = i + 1;
                UpdateHud($"V5 Test: Session {_v5TestSession}/{v5TestSessionsToRun} - Creating anchor...");
                anchorPersistence.CreateAnchor();

                yield return new WaitForSeconds(v5TestIntervalSeconds);

                UpdateHud($"V5 Test: Session {_v5TestSession}/{v5TestSessionsToRun} - Querying anchors...");
                anchorPersistence.QueryAnchors();

                yield return new WaitForSeconds(v5TestIntervalSeconds);
            }

            UpdateHud($"V5 Test complete. {_v5TestSessionsToRun} sessions executed.");
            ExportAnchorLog();

            _v5TestRunning = false;
        }

        private void HandleGeometryCollected(List<RoomBoundingBoxEntry> entries)
        {
            UpdateHud($"V4: Collected {entries.Count} room geometry entries.");
            Log($"Received {entries.Count} bounding box entries from SceneGeometryCollector.");

            if (autoExportOnCollect && entries.Count > 0)
            {
                ExportBoundingBoxes(entries);
            }
        }

        private void HandleCollectionFailed(string error)
        {
            UpdateHud($"V4: Collection failed - {error}");
            LogError($"Scene geometry collection failed: {error}");
        }

        private void HandleAnchorEvent(AnchorLogEntry entry)
        {
            _anchorLog.Add(entry);
            Log($"Anchor event: [{entry.session}] {entry.operation} uuid={entry.uuid} success={entry.success}");

            if (!string.IsNullOrEmpty(entry.errorDetail))
            {
                LogWarning($"  Error: {entry.errorDetail}");
            }

            if (entry.success && entry.operation == "create")
            {
                UpdateHud($"V5: Created anchor {entry.uuid.Substring(0, Math.Min(8, entry.uuid.Length))}...");
            }
        }

        private void HandleLocalizationComplete(List<SavedAnchorRecord> localizedAnchors)
        {
            UpdateHud($"V5: Localized {localizedAnchors.Count} anchors.");
            Log($"Localization complete: {localizedAnchors.Count} anchors localized.");
        }

        private void ExportBoundingBoxes(List<RoomBoundingBoxEntry> entries)
        {
            if (!ValidationDataExporter.TryGetExportDirectory(out string dir))
            {
                UpdateHud("V4: Export failed - cannot create export directory.");
                return;
            }

            _lastExportDirectory = dir;

            string jsonPath = ValidationDataExporter.WriteBoundingBoxJson(entries, dir, exportJsonFileName);
            string csvPath = ValidationDataExporter.WriteBoundingBoxCsv(entries, dir, exportCsvFileName);

            if (jsonPath != null && csvPath != null)
            {
                UpdateHud($"V4: Exported to {dir}");
                Log($"Exported bounding boxes to {dir}");
                ExportMeasurementComparisonTemplate(entries, dir);
            }
            else
            {
                UpdateHud("V4: Export partially failed.");
            }
        }

        private void ExportMeasurementComparisonTemplate(List<RoomBoundingBoxEntry> entries, string directoryPath)
        {
            var sb = new StringBuilder(8192);
            sb.AppendLine("label,classification,measured_sizeX_cm,measured_sizeY_cm,measured_sizeZ_cm,api_sizeX_m,api_sizeY_m,api_sizeZ_m,errorX_cm,errorY_cm,errorZ_cm,pass");
            sb.AppendLine("# Fill in measured_sizeX_cm etc. with tape-measure values, then compute pass/fail (error <= 30cm for size, <= 50cm for wall position)");
            sb.AppendLine("# This template is for manual comparison after collecting Quest 3 data.");

            foreach (var e in entries)
            {
                sb.Append(ValidationDataExporter.EscapeCsv(e.label)); sb.Append(',');
                sb.Append(ValidationDataExporter.EscapeCsv(e.classification)); sb.Append(',');
                sb.Append("0"); sb.Append(',');
                sb.Append("0"); sb.Append(',');
                sb.Append("0"); sb.Append(',');
                sb.Append(e.sizeX.ToString("F3")); sb.Append(',');
                sb.Append(e.sizeY.ToString("F3")); sb.Append(',');
                sb.Append(e.sizeZ.ToString("F3")); sb.Append(',');
                sb.Append("N/A"); sb.Append(',');
                sb.Append("N/A"); sb.Append(',');
                sb.Append("N/A"); sb.Append(',');
                sb.Append("FILL_MEASUREMENTS");
                sb.AppendLine();
            }

            string path = Path.Combine(directoryPath, "measurement_comparison_template.csv");
            try
            {
                File.WriteAllText(path, sb.ToString(), Encoding.UTF8);
                Log($"Measurement comparison template written to {path}");
            }
            catch (Exception ex)
            {
                LogError($"Failed to write comparison template: {ex.Message}");
            }
        }

        public void ExportAnchorLog()
        {
            if (_anchorLog.Count == 0)
            {
                Log("No anchor events to export.");
                return;
            }

            if (!ValidationDataExporter.TryGetExportDirectory(out string dir))
            {
                UpdateHud("V5: Export failed - cannot create export directory.");
                return;
            }

            _lastExportDirectory = dir;

            string jsonPath = ValidationDataExporter.WriteAnchorLogJson(_anchorLog, dir, anchorLogJsonFileName);
            string csvPath = ValidationDataExporter.WriteAnchorLogCsv(_anchorLog, dir, anchorLogCsvFileName);

            if (jsonPath != null && csvPath != null)
            {
                UpdateHud($"V5: Log exported to {dir}");
                Log($"Exported anchor log to {dir}");

                int successCount = 0;
                foreach (var entry in _anchorLog)
                {
                    if (entry.success) successCount++;
                }
                Log($"Anchor log summary: {successCount}/{_anchorLog.Count} successful operations across {anchorPersistence != null ? anchorPersistence.SessionId : 0} sessions.");
            }
            else
            {
                UpdateHud("V5: Log export partially failed.");
            }
        }

        private void UpdateHud(string message)
        {
            if (statusText != null)
            {
                statusText.text = message;
            }

            if (instructionText != null)
            {
                instructionText.text = $"[DEA-89 Session {anchorPersistence != null ? anchorPersistence.SessionId : 0}]\nF4=Scene | F5=Create | F6=Query | F7=V5Test | F8=Erase";
            }

            Debug.Log($"[ValidationRunner] {message}");
        }

        private void Log(string message)
        {
            Debug.Log($"[ValidationRunner] {message}");
        }

        private void LogWarning(string message)
        {
            Debug.LogWarning($"[ValidationRunner] {message}");
        }

        private void LogError(string message)
        {
            Debug.LogError($"[ValidationRunner] {message}");
        }

    }
}
