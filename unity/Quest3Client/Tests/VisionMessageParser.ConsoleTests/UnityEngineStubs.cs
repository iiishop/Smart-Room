using System;
using System.Text.Json;

namespace UnityEngine
{
    public static class JsonUtility
    {
        public static T FromJson<T>(string json)
        {
            JsonSerializerOptions options = CreateOptions();
            T? value = JsonSerializer.Deserialize<T>(json, options);
            if (value == null)
            {
                throw new InvalidOperationException("JsonUtility returned null.");
            }

            return value;
        }

        private static JsonSerializerOptions CreateOptions()
        {
            return new JsonSerializerOptions
            {
                IncludeFields = true,
                PropertyNameCaseInsensitive = false
            };
        }
    }
}
