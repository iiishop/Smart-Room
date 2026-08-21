using SmartRoom.Networking;

static void AssertEqual(string expected, string actual, string message)
{
    if (!string.Equals(expected, actual, StringComparison.Ordinal))
    {
        throw new InvalidOperationException($"{message}. Expected={expected}, Actual={actual}");
    }
}

AssertEqual("chair 0.93", VisionLabelFormatting.FormatLabel("chair", 0.934f), "formats score with two decimals");
AssertEqual("unknown 0.00", VisionLabelFormatting.FormatLabel("  ", float.NaN), "normalizes empty label and invalid score");
AssertEqual("desk 0.00", VisionLabelFormatting.FormatLabel(" desk ", float.PositiveInfinity), "trims label and clamps invalid score");
Console.WriteLine("VisionLabelFormatting console tests passed.");
