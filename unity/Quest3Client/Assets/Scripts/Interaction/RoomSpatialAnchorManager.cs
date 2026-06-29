using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using UnityEngine;

namespace SmartRoom.Interaction
{
    /// <summary>
    /// Owns the persistent Meta Spatial Anchor used to restore a room's canonical world frame.
    /// </summary>
    public sealed class RoomSpatialAnchorManager : MonoBehaviour
    {
        private const string DefaultObjectName = "RoomSpatialAnchorManager";
        private const float CreationTimeoutSeconds = 15f;
        private const double LocalizationTimeoutSeconds = 15.0;

        private static RoomSpatialAnchorManager _instance;
        private OVRSpatialAnchor _currentAnchor;
        private Pose _canonicalRoomPose = Pose.identity;
        private int _operationVersion;

        public static bool IsReady => _instance != null && _instance._currentAnchor != null;
        public static Pose CanonicalRoomPose => _instance != null ? _instance._canonicalRoomPose : Pose.identity;

        public static RoomSpatialAnchorManager EnsureExists()
        {
            if (_instance != null)
                return _instance;

            RoomSpatialAnchorManager existing = FindFirstObjectByType<RoomSpatialAnchorManager>();
            if (existing != null)
            {
                _instance = existing;
                return existing;
            }

            GameObject managerObject = GameObject.Find(DefaultObjectName);
            if (managerObject == null)
                managerObject = new GameObject(DefaultObjectName);
            _instance = managerObject.GetComponent<RoomSpatialAnchorManager>();
            if (_instance == null)
                _instance = managerObject.AddComponent<RoomSpatialAnchorManager>();
            return _instance;
        }

        public static Vector3 WorldToRoomPoint(Vector3 worldPoint)
        {
            Pose pose = CanonicalRoomPose;
            return Quaternion.Inverse(pose.rotation) * (worldPoint - pose.position);
        }

        public static Vector3 RoomToWorldPoint(Vector3 roomPoint)
        {
            Pose pose = CanonicalRoomPose;
            return pose.position + pose.rotation * roomPoint;
        }

        public void CreateAndSave(
            string roomId,
            Pose canonicalPose,
            Action<bool, string, string> completed)
        {
            int version = BeginOperation(canonicalPose);
            CreateAndSaveAsync(roomId, canonicalPose, version, completed);
        }

        public void LoadLocalizeAndAlign(
            string roomId,
            string anchorUuid,
            Pose canonicalPose,
            Action<bool, string, string> completed)
        {
            int version = BeginOperation(canonicalPose);
            LoadLocalizeAndAlignAsync(roomId, anchorUuid, canonicalPose, version, completed);
        }

        public void EraseSavedAnchor(string anchorUuid)
        {
            if (!Guid.TryParse(anchorUuid, out Guid uuid))
                return;
            EraseSavedAnchorAsync(uuid);
        }

        private void Awake()
        {
            if (_instance != null && _instance != this)
            {
                Destroy(gameObject);
                return;
            }
            _instance = this;
            gameObject.name = DefaultObjectName;
            DontDestroyOnLoad(gameObject);
        }

        private void OnDestroy()
        {
            if (_instance == this)
                _instance = null;
        }

        private int BeginOperation(Pose canonicalPose)
        {
            _operationVersion++;
            _canonicalRoomPose = canonicalPose;
            DestroyCurrentRuntimeAnchor();
            return _operationVersion;
        }

        private async void CreateAndSaveAsync(
            string roomId,
            Pose canonicalPose,
            int version,
            Action<bool, string, string> completed)
        {
            GameObject anchorObject = new GameObject("RoomSpatialAnchor_" + roomId);
            anchorObject.transform.SetPositionAndRotation(canonicalPose.position, canonicalPose.rotation);
            OVRSpatialAnchor anchor = anchorObject.AddComponent<OVRSpatialAnchor>();

            float deadline = Time.realtimeSinceStartup + CreationTimeoutSeconds;
            while (version == _operationVersion && anchor != null && !anchor.Created &&
                   Time.realtimeSinceStartup < deadline)
            {
                await Task.Yield();
            }

            if (version != _operationVersion)
            {
                if (anchorObject != null)
                    Destroy(anchorObject);
                return;
            }
            if (anchor == null || !anchor.Created)
            {
                if (anchorObject != null)
                    Destroy(anchorObject);
                completed?.Invoke(false, string.Empty, "Spatial anchor creation timed out.");
                return;
            }

            var saveResult = await anchor.SaveAnchorAsync();
            if (version != _operationVersion)
            {
                if (anchorObject != null)
                    Destroy(anchorObject);
                return;
            }
            if (!saveResult.Success)
            {
                Destroy(anchorObject);
                completed?.Invoke(false, string.Empty, "Spatial anchor save failed: " + saveResult.Status);
                return;
            }

            _currentAnchor = anchor;
            _canonicalRoomPose = canonicalPose;
            completed?.Invoke(true, anchor.Uuid.ToString(), string.Empty);
        }

        private async void LoadLocalizeAndAlignAsync(
            string roomId,
            string anchorUuid,
            Pose canonicalPose,
            int version,
            Action<bool, string, string> completed)
        {
            if (!Guid.TryParse(anchorUuid, out Guid uuid))
            {
                completed?.Invoke(false, string.Empty, "Saved spatial anchor UUID is invalid.");
                return;
            }

            var unboundAnchors = new List<OVRSpatialAnchor.UnboundAnchor>();
            var loadResult = await OVRSpatialAnchor.LoadUnboundAnchorsAsync(new[] { uuid }, unboundAnchors);
            if (version != _operationVersion)
                return;
            if (!loadResult.Success || unboundAnchors.Count == 0)
            {
                completed?.Invoke(false, string.Empty, "Spatial anchor load failed: " + loadResult.Status);
                return;
            }

            OVRSpatialAnchor.UnboundAnchor unboundAnchor = unboundAnchors[0];
            if (!unboundAnchor.Localized &&
                !await unboundAnchor.LocalizeAsync(LocalizationTimeoutSeconds))
            {
                completed?.Invoke(false, string.Empty, "Spatial anchor could not be localized in this room.");
                return;
            }
            if (version != _operationVersion)
                return;
            if (!unboundAnchor.TryGetPose(out Pose localizedPose))
            {
                completed?.Invoke(false, string.Empty, "Spatial anchor pose is currently unavailable.");
                return;
            }

            GameObject anchorObject = new GameObject("RoomSpatialAnchor_" + roomId);
            anchorObject.transform.SetPositionAndRotation(localizedPose.position, localizedPose.rotation);
            OVRSpatialAnchor anchor = anchorObject.AddComponent<OVRSpatialAnchor>();
            unboundAnchor.BindTo(anchor);

            if (!TryAlignCameraRig(localizedPose, canonicalPose, out string alignmentError))
            {
                Destroy(anchorObject);
                completed?.Invoke(false, string.Empty, alignmentError);
                return;
            }

            await Task.Yield();
            if (version != _operationVersion)
            {
                Destroy(anchorObject);
                return;
            }

            _currentAnchor = anchor;
            _canonicalRoomPose = canonicalPose;
            completed?.Invoke(true, uuid.ToString(), string.Empty);
        }

        private static bool TryAlignCameraRig(Pose localizedAnchorPose, Pose canonicalPose, out string error)
        {
            OVRCameraRig cameraRig = FindFirstObjectByType<OVRCameraRig>();
            if (cameraRig == null)
            {
                error = "OVRCameraRig was not found; room coordinates cannot be aligned.";
                return false;
            }

            Transform rig = cameraRig.transform;
            Quaternion deltaRotation = canonicalPose.rotation * Quaternion.Inverse(localizedAnchorPose.rotation);
            rig.position = canonicalPose.position + deltaRotation * (rig.position - localizedAnchorPose.position);
            rig.rotation = deltaRotation * rig.rotation;
            error = string.Empty;
            return true;
        }

        private async void EraseSavedAnchorAsync(Guid uuid)
        {
            try
            {
                if (_currentAnchor != null && _currentAnchor.Uuid == uuid)
                {
                    OVRSpatialAnchor anchorToErase = _currentAnchor;
                    var currentResult = await anchorToErase.EraseAnchorAsync();
                    if (!currentResult.Success)
                        Debug.LogWarning("[RoomSpatialAnchorManager] Anchor erase failed: " + currentResult.Status);
                    if (_currentAnchor == anchorToErase)
                        _currentAnchor = null;
                    if (anchorToErase != null)
                        Destroy(anchorToErase.gameObject);
                    return;
                }

                var unboundAnchors = new List<OVRSpatialAnchor.UnboundAnchor>();
                var loadResult = await OVRSpatialAnchor.LoadUnboundAnchorsAsync(new[] { uuid }, unboundAnchors);
                if (!loadResult.Success || unboundAnchors.Count == 0)
                    return;
                if (!unboundAnchors[0].Localized && !await unboundAnchors[0].LocalizeAsync(LocalizationTimeoutSeconds))
                    return;

                GameObject anchorObject = new GameObject("RoomSpatialAnchor_Erase");
                OVRSpatialAnchor anchor = anchorObject.AddComponent<OVRSpatialAnchor>();
                unboundAnchors[0].BindTo(anchor);
                var eraseResult = await anchor.EraseAnchorAsync();
                if (!eraseResult.Success)
                    Debug.LogWarning("[RoomSpatialAnchorManager] Anchor erase failed: " + eraseResult.Status);
                Destroy(anchorObject);
            }
            catch (Exception exc)
            {
                Debug.LogWarning("[RoomSpatialAnchorManager] Anchor erase failed: " + exc.Message);
            }
        }

        private void DestroyCurrentRuntimeAnchor()
        {
            if (_currentAnchor == null)
                return;
            Destroy(_currentAnchor.gameObject);
            _currentAnchor = null;
        }
    }
}
