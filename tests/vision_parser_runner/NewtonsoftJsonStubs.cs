using System.Text.Json;

namespace Newtonsoft.Json
{
    public sealed class JsonException : Exception
    {
        public JsonException(string message) : base(message)
        {
        }
    }
}

namespace Newtonsoft.Json.Linq
{
    using Newtonsoft.Json;

    public class JToken
    {
        private readonly JsonElement _element;

        protected JToken(JsonElement element)
        {
            _element = element;
        }

        protected JsonElement Element => _element;

        public virtual JToken? this[string propertyName] => null;

        public T Value<T>()
        {
            object? value = typeof(T) switch
            {
                var type when type == typeof(int) => _element.GetInt32(),
                var type when type == typeof(long) => _element.GetInt64(),
                var type when type == typeof(float) => _element.GetSingle(),
                var type when type == typeof(string) => _element.GetString() ?? string.Empty,
                _ => throw new NotSupportedException($"Unsupported token value type: {typeof(T).Name}")
            };

            return (T)value;
        }

        internal static JToken Wrap(JsonElement element)
        {
            return element.ValueKind switch
            {
                JsonValueKind.Object => new JObject(element),
                JsonValueKind.Array => new JArray(element),
                _ => new JValue(element)
            };
        }
    }

    public sealed class JObject : JToken
    {
        internal JObject(JsonElement element) : base(element)
        {
        }

        public override JToken? this[string propertyName]
        {
            get
            {
                if (!Element.TryGetProperty(propertyName, out JsonElement property))
                {
                    return null;
                }

                return Wrap(property);
            }
        }

        public static JObject Parse(string json)
        {
            using JsonDocument document = JsonDocument.Parse(json);
            return new JObject(document.RootElement.Clone());
        }
    }

    public sealed class JArray : JToken, IEnumerable<JToken>
    {
        internal JArray(JsonElement element) : base(element)
        {
        }

        public IEnumerator<JToken> GetEnumerator()
        {
            foreach (JsonElement item in Element.EnumerateArray())
            {
                yield return Wrap(item);
            }
        }

        System.Collections.IEnumerator System.Collections.IEnumerable.GetEnumerator()
        {
            return GetEnumerator();
        }
    }

    internal sealed class JValue : JToken
    {
        internal JValue(JsonElement element) : base(element)
        {
        }
    }
}
