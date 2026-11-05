using Npgsql;
using OpenTelemetry.Metrics;
using OpenTelemetry.Resources;
using TicketRush.Data;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddProblemDetails();
builder.Services.AddTicketRushData(builder.Configuration);

builder.Services.AddOpenTelemetry()
    .ConfigureResource(resource => resource.AddService("ticket-rush-api"))
    .WithMetrics(metrics => metrics
        .AddAspNetCoreInstrumentation()
        .AddRuntimeInstrumentation()
        .AddPrometheusExporter());

var app = builder.Build();

app.UseExceptionHandler();
app.UseStatusCodePages();

if (app.Configuration.GetValue<bool>("Database:ApplySchemaOnStartup"))
{
    await Schema.ApplyAsync(app.Services.GetRequiredService<NpgsqlDataSource>());
}

app.MapPrometheusScrapingEndpoint();

app.MapGet("/health", async (NpgsqlDataSource db, CancellationToken ct) =>
{
    await using var command = db.CreateCommand("SELECT 1");
    await command.ExecuteScalarAsync(ct);
    return Results.Ok(new { status = "healthy" });
});

// Week 1 starts here: implement the six endpoints from docs/specs/block-1-data-and-performance.md
// (API contract section), the seeder (`dotnet run -- seed`) and the hold-expiry background job.

app.Run();

public partial class Program;
