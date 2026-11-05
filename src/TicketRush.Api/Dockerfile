# Build context: repository root (see ops/docker-compose.yml).
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY global.json Directory.Build.props TicketRush.sln ./
COPY src/ src/
COPY tests/ tests/
RUN dotnet restore src/TicketRush.Api/TicketRush.Api.csproj
RUN dotnet publish src/TicketRush.Api/TicketRush.Api.csproj -c Release -o /app --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:10.0
WORKDIR /app
COPY --from=build /app .
ENV ASPNETCORE_HTTP_PORTS=8080
EXPOSE 8080
ENTRYPOINT ["dotnet", "TicketRush.Api.dll"]
