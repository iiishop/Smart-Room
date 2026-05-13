using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
#if HAS_META_XR_SCENE
using Meta.XR.Scene;
#endif

namespace SmartRoom.Validation
{
    public class SceneGeometryCollector : MonoBehaviour
    {
        [SerializeField] private KeyCode triggerKey = KeyCode.F4;
        [SerializeField] private float sceneLoadTimeoutSeconds = 10f;
        [SerializeField] private bool collectOnStart = false;
        [SerializeField] private bool logVerbose = true;

        public event Action<List<RoomBoundingBoxEntry>> OnGeometryCollected;
        public event Action<string> OnCollectionFailed;

        private bool _sceneAvailable;
        private bool _collectionInProgress;
        private float _loadingStartedAt;
        private readonly List<RoomBoundingBoxEntry> _pendingEntries = new List<RoomBoundingBoxEntry>();
        private bool _pendingExport;

        public bool IsSceneAvailable => _sceneAvailable;
        public bool IsCollecting => _collectionInProgress;

        private void Start()
        {
            if (collectOnStart)
            {
                StartCoroutine(DelayedCollect(0.5f));
            }

            Log("SceneGeometryCollector initialized. Press " + triggerKey + " to collect room geometry.");
        }

        private void Update()
        {
            if (Input.GetKeyDown(triggerKey) && !_collectionInProgress)
            {
                CollectRoomGeometry();
            }

            if (_collectionInProgress && !_sceneAvailable && Time.time > _loadingStartedAt + sceneLoadTimeoutSeconds)
            {
                _collectionInProgress = false;
                string err = "Scene model loading timed out after " + sceneLoadTimeoutSeconds + "s";
                LogError(err);
                OnCollectionFailed?.Invoke(err);
            }

            if (_pendingExport)
            {
                _pendingExport = false;
                OnGeometryCollected?.Invoke(_pendingEntries);
            }
        }

        public void CollectRoomGeometry()
        {
            if (_collectionInProgress)
            {
                Log("Collection already in progress, ignoring request.");
                return;
            }

            _collectionInProgress = true;
            StartCoroutine(CollectCoroutine());
        }

        private IEnumerator CollectCoroutine()
        {
            Log("Starting room geometry collection...");
            _pendingEntries.Clear();

#if HAS_META_XR_SCENE
            OVRSceneManager sceneManager = null;

            yield return null;

            sceneManager = FindFirstObjectByType<OVRSceneManager>();
            if (sceneManager == null)
            {
                string err = "OVRSceneManager not found in scene. Add OVRSceneManager to the scene and ensure Room Setup is complete.";
                LogError(err);
                _collectionInProgress = false;
                OnCollectionFailed?.Invoke(err);
                yield break;
            }

            _sceneAvailable = false;
            _loadingStartedAt = Time.time;

            sceneManager.SceneModelLoadedSuccessfully += OnSceneLoaded;

            Log("Waiting for OVRSceneManager to load scene model...");
            yield return new WaitUntil(() => _sceneAvailable || Time.time > _loadingStartedAt + sceneLoadTimeoutSeconds);

            sceneManager.SceneModelLoadedSuccessfully -= OnSceneLoaded;

            if (!_sceneAvailable)
            {
                string err = "Scene model did not load within timeout. Ensure Room Setup is complete in Quest system settings.";
                LogError(err);
                _collectionInProgress = false;
                OnCollectionFailed?.Invoke(err);
                yield break;
            }

            yield return null;

            OVRSceneAnchor[] anchors = FindObjectsByType<OVRSceneAnchor>(FindObjectsSortMode.None);
            if (anchors == null || anchors.Length == 0)
            {
                string err = "No OVRSceneAnchor components found in scene. Room may be empty or not scanned.";
                LogError(err);
                _collectionInProgress = false;
                OnCollectionFailed?.Invoke(err);
                yield break;
            }

            Log($"Found {anchors.Length} OVRSceneAnchor components. Extracting bounding box data...");

            long timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

            foreach (var anchor in anchors)
            {
                var entry = ExtractBoundingBox(anchor, timestamp);
                if (entry != null)
                {
                    _pendingEntries.Add(entry);
                }
            }

            Log($"Collected {_pendingEntries.Count} room geometry entries.");
#else
            LogWarning("Meta XR Scene API not available in this build. Generating placeholder geometry data.");
            yield return null;
            _pendingEntries.AddRange(GeneratePlaceholderEntries());
#endif

            _collectionInProgress = false;
            _pendingExport = true;
        }

#if HAS_META_XR_SCENE
        private void OnSceneLoaded()
        {
            _sceneAvailable = true;
            Log("Scene model loaded successfully.");
        }

        private RoomBoundingBoxEntry ExtractBoundingBox(OVRSceneAnchor anchor, long timestamp)
        {
            if (anchor == null)
            {
                return null;
            }

            var entry = new RoomBoundingBoxEntry
            {
                label = anchor.name,
                timestampMs = timestamp
            };

            var classification = anchor.GetComponent<OVRSemanticClassification>();
            if (classification != null && !string.IsNullOrEmpty(classification.Labels))
            {
                entry.classification = classification.Labels;
            }
            else if (!string.IsNullOrEmpty(anchor.name))
            {
                entry.classification = GuessClassification(anchor.name);
            }
            else
            {
                entry.classification = "unknown";
            }

            Transform t = anchor.transform;
            entry.positionX = t.position.x;
            entry.positionY = t.position.y;
            entry.positionZ = t.position.z;

            Vector3 euler = t.rotation.eulerAngles;
            entry.rotationX = euler.x;
            entry.rotationY = euler.y;
            entry.rotationZ = euler.z;

            Collider col = anchor.GetComponent<Collider>();
            if (col != null)
            {
                Bounds b = col.bounds;
                entry.centerX = b.center.x;
                entry.centerY = b.center.y;
                entry.centerZ = b.center.z;
                entry.sizeX = b.size.x;
                entry.sizeY = b.size.y;
                entry.sizeZ = b.size.z;
            }
            else
            {
                Renderer rend = anchor.GetComponent<Renderer>();
                if (rend != null)
                {
                    Bounds b = rend.bounds;
                    entry.centerX = b.center.x;
                    entry.centerY = b.center.y;
                    entry.centerZ = b.center.z;
                    entry.sizeX = b.size.x;
                    entry.sizeY = b.size.y;
                    entry.sizeZ = b.size.z;
                }
                else
                {
                    entry.sizeX = 0f;
                    entry.sizeY = 0f;
                    entry.sizeZ = 0f;
                }
            }

            if (logVerbose)
            {
                Log($"  [{entry.classification}] {entry.label}: center=({entry.centerX:F3},{entry.centerY:F3},{entry.centerZ:F3}) size=({entry.sizeX:F3},{entry.sizeY:F3},{entry.sizeZ:F3})");
            }

            return entry;
        }

        private static string GuessClassification(string name)
        {
            string lower = name.ToLowerInvariant();
            if (lower.Contains("floor")) return "FLOOR";
            if (lower.Contains("wall")) return "WALL_FACE";
            if (lower.Contains("ceiling")) return "CEILING";
            if (lower.Contains("table") || lower.Contains("desk")) return "TABLE";
            if (lower.Contains("couch") || lower.Contains("sofa")) return "COUCH";
            if (lower.Contains("door")) return "DOOR_FRAME";
            if (lower.Contains("window")) return "WINDOW_FRAME";
            if (lower.Contains("chair") || lower.Contains("seat")) return "CHAIR";
            if (lower.Contains("screen") || lower.Contains("tv") || lower.Contains("monitor")) return "SCREEN";
            if (lower.Contains("bed")) return "BED";
            if (lower.Contains("storage") || lower.Contains("cabinet")) return "STORAGE";
            return "OTHER";
        }
#else
        private static List<RoomBoundingBoxEntry> GeneratePlaceholderEntries()
        {
            long timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            return new List<RoomBoundingBoxEntry>
            {
                new RoomBoundingBoxEntry { label = "Floor", classification = "FLOOR", positionX = 0, positionY = -1.0f, positionZ = 0, centerX = 0, centerY = -1.0f, centerZ = 0, sizeX = 5.0f, sizeY = 0.02f, sizeZ = 4.0f, rotationX = 0, rotationY = 0, rotationZ = 0, timestampMs = timestamp },
                new RoomBoundingBoxEntry { label = "Ceiling", classification = "CEILING", positionX = 0, positionY = 2.5f, positionZ = 0, centerX = 0, centerY = 2.5f, centerZ = 0, sizeX = 5.0f, sizeY = 0.02f, sizeZ = 4.0f, rotationX = 0, rotationY = 0, rotationZ = 0, timestampMs = timestamp },
                new RoomBoundingBoxEntry { label = "Wall_North", classification = "WALL_FACE", positionX = 0, positionY = 0.75f, positionZ = 2.0f, centerX = 0, centerY = 0.75f, centerZ = 2.0f, sizeX = 5.0f, sizeY = 2.5f, sizeZ = 0.02f, rotationX = 0, rotationY = 0, rotationZ = 0, timestampMs = timestamp },
                new RoomBoundingBoxEntry { label = "Wall_South", classification = "WALL_FACE", positionX = 0, positionY = 0.75f, positionZ = -2.0f, centerX = 0, centerY = 0.75f, centerZ = -2.0f, sizeX = 5.0f, sizeY = 2.5f, sizeZ = 0.02f, rotationX = 0, rotationY = 0, rotationZ = 0, timestampMs = timestamp },
                new RoomBoundingBoxEntry { label = "Wall_East", classification = "WALL_FACE", positionX = 2.5f, positionY = 0.75f, positionZ = 0, centerX = 2.5f, centerY = 0.75f, centerZ = 0, sizeX = 0.02f, sizeY = 2.5f, sizeZ = 4.0f, rotationX = 0, rotationY = 0, rotationZ = 0, timestampMs = timestamp },
                new RoomBoundingBoxEntry { label = "Wall_West", classification = "WALL_FACE", positionX = -2.5f, positionY = 0.75f, positionZ = 0, centerX = -2.5f, centerY = 0.75f, centerZ = 0, sizeX = 0.02f, sizeY = 2.5f, sizeZ = 4.0f, rotationX = 0, rotationY = 0, rotationZ = 0, timestampMs = timestamp },
            };
        }
#endif

        private IEnumerator DelayedCollect(float delaySeconds)
        {
            yield return new WaitForSeconds(delaySeconds);
            CollectRoomGeometry();
        }

        private void Log(string message)
        {
            Debug.Log($"[SceneGeometry] {message}");
        }

        private void LogWarning(string message)
        {
            Debug.LogWarning($"[SceneGeometry] {message}");
        }

        private void LogError(string message)
        {
            Debug.LogError($"[SceneGeometry] {message}");
        }
    }
}
