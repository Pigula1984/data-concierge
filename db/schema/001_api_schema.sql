-- Schema of the read procedures the agent may call (architecture, section 5.4).
-- Owned by dbo like the AdventureWorks tables, so ownership chaining lets agent_reader
-- run the procedures without any permission on the tables (section 5.5).
IF SCHEMA_ID(N'api') IS NULL
    EXEC (N'CREATE SCHEMA api AUTHORIZATION dbo');
GO
-- Only when someone changed the owner: a change of owner drops the grants on the schema.
IF (SELECT USER_NAME(principal_id) FROM sys.schemas WHERE name = N'api') <> N'dbo'
    ALTER AUTHORIZATION ON SCHEMA::api TO dbo;
GO
