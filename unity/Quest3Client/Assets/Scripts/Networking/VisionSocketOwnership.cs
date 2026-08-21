using System.Net.WebSockets;

namespace SmartRoom.Networking
{
    internal static class VisionSocketOwnership
    {
        public static bool ShouldClearCurrentSocket(ClientWebSocket currentSocket, ClientWebSocket socketToDispose)
        {
            return ReferenceEquals(currentSocket, socketToDispose);
        }
    }
}
