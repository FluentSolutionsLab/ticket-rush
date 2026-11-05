using System.Net;
using Npgsql;

namespace TicketRush.Tests;

[Collection("postgres")]
public sealed class SmokeTests(PostgresFixture postgres)
{
    [Fact]
    public async Task Health_returns_ok_when_database_is_reachable()
    {
        await using var factory = new ApiFactory(postgres.ConnectionString);
        using var client = factory.CreateClient();

        var response = await client.GetAsync("/health");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task Schema_is_applied_on_startup()
    {
        await using var factory = new ApiFactory(postgres.ConnectionString);
        using var client = factory.CreateClient(); // starts the app, which applies the schema

        await using var dataSource = NpgsqlDataSource.Create(postgres.ConnectionString);
        await using var command = dataSource.CreateCommand(
            "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('events','seats','bookings','booking_seats')");
        var tables = (long)(await command.ExecuteScalarAsync())!;

        Assert.Equal(4, tables);
    }

    [Fact]
    public async Task Metrics_endpoint_is_exposed()
    {
        await using var factory = new ApiFactory(postgres.ConnectionString);
        using var client = factory.CreateClient();

        var response = await client.GetAsync("/metrics");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    // Week 1: add the 200-task concurrency test from the spec ("Invariants and correctness checks").
    // It should fail against the naive strategy; that failure is the point of week 1.
}
