using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEngine;

namespace SmartRoom.Validation
{
    [Serializable]
    public class RoomBoundingBoxEntry
    {
        public string label;
        public string classification;
        public float centerX;
        public float centerY;
        public float centerZ;
        public float sizeX;
        public float sizeY;
        public float sizeZ;
        public float positionX;
        public float positionY;
        public float positionZ;
        public float rotationX;
        public float rotationY;
        public float rotationZ;
        public long timestampMs;
    }

    [Serializable]
    public class AnchorLogEntry
    {
        public int session;
        public string operation;
        public string uuid;
        public float posX;
        public float posY;
        public float posZ;
        public float rotX;
        public float rotY;
        public float rotZ;
        public bool success;
        public string errorDetail;
        public long timestampMs;
    }

    public static class ValidationDataExporter
    {
        private const int FormatVersion = 1;

        public static string WriteBoundingBoxJson(List<RoomBoundingBoxEntry> entries, string directoryPath, string fileName)
        {
            var sb = new StringBuilder(65536);
            sb.AppendLine("{");
            sb.AppendLine("  \"format_version\": " + FormatVersion + ",");
            sb.AppendLine("  \"exported_at_utc\": \"" + DateTime.UtcNow.ToString("O") + "\",");
            sb.AppendLine("  \"device_model\": \"" + SystemInfo.deviceModel + "\",");
            sb.AppendLine("  \"device_unique_id\": \"" + SystemInfo.deviceUniqueIdentifier + "\",");
            sb.AppendLine("  \"entries\": [");

            for (int i = 0; i < entries.Count; i++)
            {
                var e = entries[i];
                string comma = i < entries.Count - 1 ? "," : "";
                sb.AppendLine("    {");
                sb.AppendLine("      \"label\": \"" + EscapeJson(e.label) + "\",");
                sb.AppendLine("      \"classification\": \"" + EscapeJson(e.classification) + "\",");
                sb.Append("      \"centerX\": "); sb.Append(FloatString(e.centerX)); sb.AppendLine(",");
                sb.Append("      \"centerY\": "); sb.Append(FloatString(e.centerY)); sb.AppendLine(",");
                sb.Append("      \"centerZ\": "); sb.Append(FloatString(e.centerZ)); sb.AppendLine(",");
                sb.Append("      \"sizeX\": "); sb.Append(FloatString(e.sizeX)); sb.AppendLine(",");
                sb.Append("      \"sizeY\": "); sb.Append(FloatString(e.sizeY)); sb.AppendLine(",");
                sb.Append("      \"sizeZ\": "); sb.Append(FloatString(e.sizeZ)); sb.AppendLine(",");
                sb.Append("      \"positionX\": "); sb.Append(FloatString(e.positionX)); sb.AppendLine(",");
                sb.Append("      \"positionY\": "); sb.Append(FloatString(e.positionY)); sb.AppendLine(",");
                sb.Append("      \"positionZ\": "); sb.Append(FloatString(e.positionZ)); sb.AppendLine(",");
                sb.Append("      \"rotationX\": "); sb.Append(FloatString(e.rotationX)); sb.AppendLine(",");
                sb.Append("      \"rotationY\": "); sb.Append(FloatString(e.rotationY)); sb.AppendLine(",");
                sb.Append("      \"rotationZ\": "); sb.Append(FloatString(e.rotationZ)); sb.AppendLine(",");
                sb.Append("      \"timestampMs\": "); sb.Append(e.timestampMs.ToString()); sb.AppendLine("");
                sb.Append("    }" + comma);
                if (!string.IsNullOrEmpty(comma))
                {
                    sb.AppendLine("");
                }
            }

            sb.AppendLine("");
            sb.AppendLine("  ]");
            sb.AppendLine("}");

            return WriteFile(directoryPath, fileName, sb.ToString());
        }

        public static string WriteBoundingBoxCsv(List<RoomBoundingBoxEntry> entries, string directoryPath, string fileName)
        {
            var sb = new StringBuilder(32768);
            sb.AppendLine("label,classification,centerX,centerY,centerZ,sizeX,sizeY,sizeZ,positionX,positionY,positionZ,rotationX,rotationY,rotationZ,timestampMs");

            foreach (var e in entries)
            {
                sb.Append(EscapeCsv(e.label)); sb.Append(',');
                sb.Append(EscapeCsv(e.classification)); sb.Append(',');
                sb.Append(FloatString(e.centerX)); sb.Append(',');
                sb.Append(FloatString(e.centerY)); sb.Append(',');
                sb.Append(FloatString(e.centerZ)); sb.Append(',');
                sb.Append(FloatString(e.sizeX)); sb.Append(',');
                sb.Append(FloatString(e.sizeY)); sb.Append(',');
                sb.Append(FloatString(e.sizeZ)); sb.Append(',');
                sb.Append(FloatString(e.positionX)); sb.Append(',');
                sb.Append(FloatString(e.positionY)); sb.Append(',');
                sb.Append(FloatString(e.positionZ)); sb.Append(',');
                sb.Append(FloatString(e.rotationX)); sb.Append(',');
                sb.Append(FloatString(e.rotationY)); sb.Append(',');
                sb.Append(FloatString(e.rotationZ)); sb.Append(',');
                sb.Append(e.timestampMs);
                sb.AppendLine();
            }

            return WriteFile(directoryPath, fileName, sb.ToString());
        }

        public static string WriteAnchorLogJson(List<AnchorLogEntry> entries, string directoryPath, string fileName)
        {
            var sb = new StringBuilder(32768);
            sb.AppendLine("{");
            sb.AppendLine("  \"format_version\": " + FormatVersion + ",");
            sb.AppendLine("  \"exported_at_utc\": \"" + DateTime.UtcNow.ToString("O") + "\",");
            sb.AppendLine("  \"device_model\": \"" + SystemInfo.deviceModel + "\",");
            sb.AppendLine("  \"entries\": [");

            for (int i = 0; i < entries.Count; i++)
            {
                var e = entries[i];
                string comma = i < entries.Count - 1 ? "," : "";
                sb.AppendLine("    {");
                sb.Append("      \"session\": "); sb.Append(e.session); sb.AppendLine(",");
                sb.Append("      \"operation\": \""); sb.Append(EscapeJson(e.operation)); sb.AppendLine("\",");
                sb.Append("      \"uuid\": \""); sb.Append(EscapeJson(e.uuid)); sb.AppendLine("\",");
                sb.Append("      \"posX\": "); sb.Append(FloatString(e.posX)); sb.AppendLine(",");
                sb.Append("      \"posY\": "); sb.Append(FloatString(e.posY)); sb.AppendLine(",");
                sb.Append("      \"posZ\": "); sb.Append(FloatString(e.posZ)); sb.AppendLine(",");
                sb.Append("      \"rotX\": "); sb.Append(FloatString(e.rotX)); sb.AppendLine(",");
                sb.Append("      \"rotY\": "); sb.Append(FloatString(e.rotY)); sb.AppendLine(",");
                sb.Append("      \"rotZ\": "); sb.Append(FloatString(e.rotZ)); sb.AppendLine(",");
                sb.Append("      \"success\": "); sb.Append(e.success ? "true" : "false"); sb.AppendLine(",");
                sb.Append("      \"errorDetail\": \""); sb.Append(EscapeJson(e.errorDetail)); sb.AppendLine("\",");
                sb.Append("      \"timestampMs\": "); sb.Append(e.timestampMs.ToString()); sb.AppendLine("");
                sb.Append("    }" + comma);
                if (!string.IsNullOrEmpty(comma))
                {
                    sb.AppendLine("");
                }
            }

            sb.AppendLine("");
            sb.AppendLine("  ]");
            sb.AppendLine("}");

            return WriteFile(directoryPath, fileName, sb.ToString());
        }

        public static string WriteAnchorLogCsv(List<AnchorLogEntry> entries, string directoryPath, string fileName)
        {
            var sb = new StringBuilder(16384);
            sb.AppendLine("session,operation,uuid,posX,posY,posZ,rotX,rotY,rotZ,success,errorDetail,timestampMs");

            foreach (var e in entries)
            {
                sb.Append(e.session); sb.Append(',');
                sb.Append(EscapeCsv(e.operation)); sb.Append(',');
                sb.Append(EscapeCsv(e.uuid)); sb.Append(',');
                sb.Append(FloatString(e.posX)); sb.Append(',');
                sb.Append(FloatString(e.posY)); sb.Append(',');
                sb.Append(FloatString(e.posZ)); sb.Append(',');
                sb.Append(FloatString(e.rotX)); sb.Append(',');
                sb.Append(FloatString(e.rotY)); sb.Append(',');
                sb.Append(FloatString(e.rotZ)); sb.Append(',');
                sb.Append(e.success ? "true" : "false"); sb.Append(',');
                sb.Append(EscapeCsv(e.errorDetail ?? ""));
                sb.Append(',');
                sb.Append(e.timestampMs);
                sb.AppendLine();
            }

            return WriteFile(directoryPath, fileName, sb.ToString());
        }

        public static bool TryGetExportDirectory(out string directoryPath)
        {
            directoryPath = null;

            try
            {
                directoryPath = Path.Combine(Application.persistentDataPath, "validation_export");
                Directory.CreateDirectory(directoryPath);
                return true;
            }
            catch (Exception ex)
            {
                Debug.LogError($"[ValidationExporter] Failed to create export directory: {ex.Message}");
                return false;
            }
        }

        private static string WriteFile(string directoryPath, string fileName, string content)
        {
            string fullPath = Path.Combine(directoryPath, fileName);
            try
            {
                File.WriteAllText(fullPath, content, Encoding.UTF8);
                Debug.Log($"[ValidationExporter] Exported {fileName} ({content.Length} bytes) to {fullPath}");
                return fullPath;
            }
            catch (Exception ex)
            {
                Debug.LogError($"[ValidationExporter] Failed to write {fileName}: {ex.Message}");
                return null;
            }
        }

        private static string FloatString(float value)
        {
            return ((double)value).ToString("F4");
        }

        private static string EscapeJson(string value)
        {
            if (string.IsNullOrEmpty(value))
            {
                return "";
            }

            var sb = new StringBuilder(value.Length + 2);
            foreach (char c in value)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\b': sb.Append("\\b"); break;
                    case '\f': sb.Append("\\f"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < 32)
                        {
                            sb.Append($"\\u{(int)c:X4}");
                        }
                        else
                        {
                            sb.Append(c);
                        }
                        break;
                }
            }
            return sb.ToString();
        }

        internal static string EscapeCsv(string value)
        {
            if (string.IsNullOrEmpty(value))
            {
                return "";
            }

            if (value.Contains(",") || value.Contains("\"") || value.Contains("\n") || value.Contains("\r"))
            {
                return "\"" + value.Replace("\"", "\"\"") + "\"";
            }

            return value;
        }
    }
}
