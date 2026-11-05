using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Npgsql;

namespace TicketRush.Data;

public static class DataServiceCollectionExtensions
{
    /// <summary>
    /// Registers a pooled <see cref="NpgsqlDataSource"/> for the "TicketRush" connection string.
    /// Week 1: add EF Core (and later Dapper) here. Week 4: register a second data source for the replica.
    /// </summary>
    public static IServiceCollection AddTicketRushData(this IServiceCollection services, IConfiguration configuration)
    {
        var connectionString = configuration.GetConnectionString("TicketRush")
            ?? throw new InvalidOperationException("Connection string 'TicketRush' is not configured.");

        services.AddSingleton(_ => NpgsqlDataSource.Create(connectionString));
        return services;
    }
}
