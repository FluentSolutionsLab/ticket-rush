using System.Reflection;
using Npgsql;

namespace TicketRush.Data;

/// <summary>Applies the embedded SQL scripts in order. Idempotent: every script uses IF NOT EXISTS.</summary>
public static class Schema
{
    public static async Task ApplyAsync(NpgsqlDataSource dataSource, CancellationToken cancellationToken = default)
    {
        var assembly = typeof(Schema).Assembly;
        var scripts = assembly.GetManifestResourceNames()
            .Where(name => name.EndsWith(".sql", StringComparison.Ordinal))
            .Order(StringComparer.Ordinal);

        foreach (var name in scripts)
        {
            await using var stream = assembly.GetManifestResourceStream(name)!;
            using var reader = new StreamReader(stream);
            var sql = await reader.ReadToEndAsync(cancellationToken);

            await using var command = dataSource.CreateCommand(sql);
            await command.ExecuteNonQueryAsync(cancellationToken);
        }
    }
}
