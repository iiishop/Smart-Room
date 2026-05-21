namespace SmartRoom.Networking
{
    internal static class VisionWorldPositionFactory
    {
        public static WorldPosition Create(
            int objectId,
            string label,
            float score,
            float x,
            float y,
            float z,
            float depthM)
        {
            return new WorldPosition(objectId, label, score, x, y, z, depthM);
        }
    }
}
