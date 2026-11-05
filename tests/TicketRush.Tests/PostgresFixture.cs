using Testcontainers.PostgreSql;

namespace TicketRush.Tests;

/// <summary>One throwaway Postgres container shared by every test in the "postgres" collection.</summary>
public sealed class PostgresFixture : IAsyncLifetime
{
    private readonly PostgreSqlContainer _container = new PostgreSqlBuilder("postgres:17")
        .WithDatabase("ticketrush")
        .WithUsername("ticketrush")
        .WithPassword("ticketrush")
        .Build();

    public string ConnectionString => _container.GetConnectionString();

    public Task InitializeAsync() => _container.StartAsync();

    public Task DisposeAsync() => _container.DisposeAsync().AsTask();
}

[CollectionDefinition("postgres")]
public sealed class PostgresCollection : ICollectionFixture<PostgresFixture>;
