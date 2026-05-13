using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
#if HAS_META_XR_SCENE
using Meta.XR.Scene;
#endif

namespace SmartRoom.Validation
{
    [Serializable]
    public class SavedAnchorRecord
    {
        public string uuid;
        public float posX;
        public float posY;
        public float posZ;
        public float rotX;
        public float rotY;
        public float rotZ;
        public long savedAtTimestampMs;
    }

    public class SpatialAnchorPersistence : MonoBehaviour
    {
        [SerializeField] private KeyCode createAnchorKey = KeyCode.F5;
        [SerializeField] private KeyCode queryAnchorKey = KeyCode.F6;
        [SerializeField] private KeyCode eraseAllKey = KeyCode.F8;
        [SerializeField] private Vector3 defaultAnchorLocalOffset = new Vector3(0f, 1.2f, 2f);
        [SerializeField] private GameObject anchorPrefab;
        [SerializeField] private bool autoCreateOnStart = false;
        [SerializeField] private float anchorCreationTimeoutSeconds = 10f;

        public event Action<AnchorLogEntry> OnAnchorEvent;
        public event Action<List<SavedAnchorRecord>> OnLocalizationComplete;

        public List<SavedAnchorRecord> SavedAnchors { get; private set; } = new List<SavedAnchorRecord>();
        public List<SavedAnchorRecord> LocalizedAnchors { get; private set; } = new List<SavedAnchorRecord>();
        public int SessionId { get; private set; }

        private const string SessionKey = "dea89_anchor_session_id";
        private const string AnchorStorageKeyPrefix = "dea89_anchor_";

        private void Awake()
        {
            SessionId = PlayerPrefs.GetInt(SessionKey, 0) + 1;
            PlayerPrefs.SetInt(SessionKey, SessionId);
            PlayerPrefs.Save();

            LoadAnchorRecordsFromPrefs();
        }

        private void Start()
        {
            Log($"SpatialAnchorPersistence initialized. Session={SessionId}, Saved anchors={SavedAnchors.Count}");
            Log($"  Create: {createAnchorKey} | Query: {queryAnchorKey} | Erase all: {eraseAllKey}");

            if (autoCreateOnStart)
            {
                StartCoroutine(DelayedCreate(1f));
            }
        }

        private void Update()
        {
            if (Input.GetKeyDown(createAnchorKey))
            {
                CreateAnchor();
            }

            if (Input.GetKeyDown(queryAnchorKey))
            {
                QueryAnchors();
            }

            if (Input.GetKeyDown(eraseAllKey))
            {
                EraseAllAnchors();
            }
        }

        public void CreateAnchor()
        {
            Vector3 worldPos = AnchorWorldPosition();
            StartCoroutine(CreateAnchorCoroutine(worldPos));
        }

        public void QueryAnchors()
        {
            StartCoroutine(QueryAnchorsCoroutine());
        }

        public void EraseAllAnchors()
        {
            StartCoroutine(EraseAllCoroutine());
        }

        public Vector3 AnchorWorldPosition()
        {
            Transform cam = Camera.main?.transform;
            if (cam != null)
            {
                return cam.position + cam.TransformDirection(defaultAnchorLocalOffset);
            }

            return defaultAnchorLocalOffset;
        }

        private IEnumerator CreateAnchorCoroutine(Vector3 worldPosition)
        {
            long timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            Log($"Creating anchor at world position ({worldPosition.x:F3}, {worldPosition.y:F3}, {worldPosition.z:F3})");

#if HAS_META_XR_SCENE
            OVRSpatialAnchor anchor = null;

            if (anchorPrefab != null)
            {
                GameObject instance = Instantiate(anchorPrefab, worldPosition, Quaternion.identity);
                anchor = instance.GetComponent<OVRSpatialAnchor>();
            }

            if (anchor == null)
            {
                GameObject go = new GameObject("SpatialAnchor_" + Guid.NewGuid().ToString("N").Substring(0, 8));
                go.transform.position = worldPosition;
                go.transform.rotation = Quaternion.identity;
                anchor = go.AddComponent<OVRSpatialAnchor>();
            }

            bool created = false;
            float startTime = Time.time;

            while (!created && Time.time < startTime + anchorCreationTimeoutSeconds)
            {
                if (anchor.Created)
                {
                    created = true;
                    break;
                }
                yield return null;
            }

            if (!created)
            {
                LogError("Anchor creation timed out.");
                EmitAnchorEvent("create", "", worldPosition, false, "Creation timed out", timestamp);
                yield break;
            }

            Guid anchorUuid = anchor.Uuid;
            string uuidStr = anchorUuid.ToString();
            Log($"Anchor created. UUID={uuidStr}");

            OVRSpatialAnchor.SaveOptions saveOptions = new OVRSpatialAnchor.SaveOptions
            {
                Storage = OVRSpace.StorageLocation.Local
            };

            bool saved = false;
            anchor.Save(saveOptions, (resultAnchor, success) =>
            {
                saved = success;
            });

            float saveStartTime = Time.time;
            while (!saved && Time.time < saveStartTime + anchorCreationTimeoutSeconds)
            {
                yield return null;
            }

            if (!saved)
            {
                LogError("Anchor save timed out.");
                EmitAnchorEvent("create-save", uuidStr, worldPosition, false, "Save timed out", timestamp);
                yield break;
            }

            AddAnchorRecord(uuidStr, worldPosition, Quaternion.identity, timestamp);
            EmitAnchorEvent("create", uuidStr, worldPosition, true, "", timestamp);
            Log($"Anchor saved successfully. UUID={uuidStr}");
#else
            LogWarning("Meta XR Spatial Anchor API not available. Creating simulated anchor record.");
            string simulatedUuid = Guid.NewGuid().ToString();
            AddAnchorRecord(simulatedUuid, worldPosition, Quaternion.identity, timestamp);
            EmitAnchorEvent("create-simulated", simulatedUuid, worldPosition, true, "Simulated (no Meta XR SDK)", timestamp);
            yield return null;
#endif
        }

        private IEnumerator QueryAnchorsCoroutine()
        {
            long timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            Log("Querying anchors...");
            LocalizedAnchors.Clear();

#if HAS_META_XR_SCENE
            if (SavedAnchors.Count == 0)
            {
                Log("No saved anchors to query.");
                OnLocalizationComplete?.Invoke(LocalizedAnchors);
                yield break;
            }

            List<OVRAnchor> unboundAnchors = new List<OVRAnchor>();

            var fetchTask = OVRSpatialAnchor.LoadUnboundAnchorsAsync(unboundAnchors);
            while (!fetchTask.IsCompleted)
            {
                yield return null;
            }

            if (unboundAnchors.Count == 0)
            {
                Log("No unbound anchors found on device.");
                OnLocalizationComplete?.Invoke(LocalizedAnchors);
                yield break;
            }

            Log($"Found {unboundAnchors.Count} unbound anchors. Localizing...");

            foreach (var unboundAnchor in unboundAnchors)
            {
                var trackingTask = unboundAnchor.Track();
                while (!trackingTask.IsCompleted)
                {
                    yield return null;
                }

                bool localized = trackingTask.Result;
                if (localized)
                {
                    var spatialAnchor = unboundAnchor.GetComponent<OVRSpatialAnchor>();
                    if (spatialAnchor != null)
                    {
                        string uuidStr = spatialAnchor.Uuid.ToString();
                        Vector3 pos = spatialAnchor.transform.position;
                        Quaternion rot = spatialAnchor.transform.rotation;

                        var record = new SavedAnchorRecord
                        {
                            uuid = uuidStr,
                            posX = pos.x,
                            posY = pos.y,
                            posZ = pos.z,
                            rotX = rot.eulerAngles.x,
                            rotY = rot.eulerAngles.y,
                            rotZ = rot.eulerAngles.z,
                            savedAtTimestampMs = timestamp,
                        };

                        LocalizedAnchors.Add(record);

                        var existing = SavedAnchors.Find(a => a.uuid == uuidStr);
                        if (existing != null)
                        {
                            float dx = pos.x - existing.posX;
                            float dy = pos.y - existing.posY;
                            float dz = pos.z - existing.posZ;
                            float drift = Mathf.Sqrt(dx * dx + dy * dy + dz * dz);
                            Log($"  Anchor {uuidStr}: drift={drift:F3}m (Δx={dx:F3}, Δy={dy:F3}, Δz={dz:F3})");
                            EmitAnchorEvent("query-drift", uuidStr, pos, true, $"Drift={drift:F3}m", timestamp);
                        }
                        else
                        {
                            EmitAnchorEvent("query-new", uuidStr, pos, true, "", timestamp);
                        }
                    }
                }
                else
                {
                    EmitAnchorEvent("query-failed", "unknown", Vector3.zero, false, "Localization failed", timestamp);
                }
            }

            Log($"Localization complete. Localized={LocalizedAnchors.Count}/{unboundAnchors.Count} anchors.");
#else
            LogWarning("Meta XR Spatial Anchor API not available. Returning simulated results.");
            foreach (var saved in SavedAnchors)
            {
                LocalizedAnchors.Add(new SavedAnchorRecord
                {
                    uuid = saved.uuid,
                    posX = saved.posX + UnityEngine.Random.Range(-0.05f, 0.05f),
                    posY = saved.posY + UnityEngine.Random.Range(-0.05f, 0.05f),
                    posZ = saved.posZ + UnityEngine.Random.Range(-0.05f, 0.05f),
                    rotX = saved.rotX,
                    rotY = saved.rotY,
                    rotZ = saved.rotZ,
                    savedAtTimestampMs = timestamp,
                });
            }
            yield return null;
#endif

            OnLocalizationComplete?.Invoke(LocalizedAnchors);
        }

        private IEnumerator EraseAllCoroutine()
        {
            Log("Erasing all saved anchors...");

#if HAS_META_XR_SCENE
            var anchors = FindObjectsByType<OVRSpatialAnchor>(FindObjectsSortMode.None);

            foreach (var anchor in anchors)
            {
                var eraseTask = anchor.EraseAsync();
                while (!eraseTask.IsCompleted)
                {
                    yield return null;
                }

                if (anchor.gameObject != null)
                {
                    Destroy(anchor.gameObject);
                }
            }
#endif

            SavedAnchors.Clear();
            ClearAllAnchorPrefs();
            Log("All anchors erased.");
            yield return null;
        }

        private void AddAnchorRecord(string uuid, Vector3 position, Quaternion rotation, long timestamp)
        {
            var record = new SavedAnchorRecord
            {
                uuid = uuid,
                posX = position.x,
                posY = position.y,
                posZ = position.z,
                rotX = rotation.eulerAngles.x,
                rotY = rotation.eulerAngles.y,
                rotZ = rotation.eulerAngles.z,
                savedAtTimestampMs = timestamp,
            };

            SavedAnchors.Add(record);
            SaveAnchorRecordToPrefs(record);
        }

        private void SaveAnchorRecordToPrefs(SavedAnchorRecord record)
        {
            string json = JsonUtility.ToJson(record);
            PlayerPrefs.SetString(AnchorStorageKeyPrefix + record.uuid, json);
            PlayerPrefs.SetInt(AnchorStorageKeyPrefix + "count", SavedAnchors.Count);
            PlayerPrefs.Save();
        }

        private void LoadAnchorRecordsFromPrefs()
        {
            SavedAnchors.Clear();
            int count = PlayerPrefs.GetInt(AnchorStorageKeyPrefix + "count", 0);

            for (int i = 0; i < count; i++)
            {
                var keys = PlayerPrefs.GetString(AnchorStorageKeyPrefix + "keys_" + i, "");
                if (string.IsNullOrEmpty(keys))
                {
                    continue;
                }

                foreach (string uuid in keys.Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries))
                {
                    string json = PlayerPrefs.GetString(AnchorStorageKeyPrefix + uuid, "");
                    if (!string.IsNullOrEmpty(json))
                    {
                        try
                        {
                            var record = JsonUtility.FromJson<SavedAnchorRecord>(json);
                            if (record != null)
                            {
                                SavedAnchors.Add(record);
                            }
                        }
                        catch
                        {
                            LogWarning($"Failed to deserialize anchor record for {uuid}");
                        }
                    }
                }
            }
        }

        private void ClearAllAnchorPrefs()
        {
            int count = PlayerPrefs.GetInt(AnchorStorageKeyPrefix + "count", 0);
            for (int i = 0; i < count; i++)
            {
                string keys = PlayerPrefs.GetString(AnchorStorageKeyPrefix + "keys_" + i, "");
                if (!string.IsNullOrEmpty(keys))
                {
                    foreach (string uuid in keys.Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries))
                    {
                        PlayerPrefs.DeleteKey(AnchorStorageKeyPrefix + uuid);
                    }
                }
                PlayerPrefs.DeleteKey(AnchorStorageKeyPrefix + "keys_" + i);
            }
            PlayerPrefs.DeleteKey(AnchorStorageKeyPrefix + "count");
            PlayerPrefs.Save();
        }

        private void EmitAnchorEvent(string operation, string uuid, Vector3 position, bool success, string errorDetail, long timestamp)
        {
            AnchorLogEntry entry = new AnchorLogEntry
            {
                session = SessionId,
                operation = operation,
                uuid = uuid,
                posX = position.x,
                posY = position.y,
                posZ = position.z,
                rotX = 0,
                rotY = 0,
                rotZ = 0,
                success = success,
                errorDetail = errorDetail ?? "",
                timestampMs = timestamp,
            };

            OnAnchorEvent?.Invoke(entry);
        }

        private void Log(string message)
        {
            Debug.Log($"[AnchorPersistence] {message}");
        }

        private void LogWarning(string message)
        {
            Debug.LogWarning($"[AnchorPersistence] {message}");
        }

        private void LogError(string message)
        {
            Debug.LogError($"[AnchorPersistence] {message}");
        }
    }
}
