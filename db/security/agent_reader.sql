-- SQL login and database user of the agent with EXECUTE on the catalog's read procedures
-- only (architecture, section 5.5). The password comes from the sqlcmd variable
-- AgentReaderPassword, which deploy.ps1 sets from DC_SQL_PASSWORD; it never enters the
-- repository. A rerun keeps the account and sets the password to the configured one.
USE master;
GO
IF SUSER_ID(N'agent_reader') IS NULL
    CREATE LOGIN agent_reader
        WITH PASSWORD = N'$(AgentReaderPassword)',
             DEFAULT_DATABASE = [$(DatabaseName)],
             CHECK_POLICY = ON,
             CHECK_EXPIRATION = OFF;
ELSE
    ALTER LOGIN agent_reader WITH PASSWORD = N'$(AgentReaderPassword)';
GO
USE [$(DatabaseName)];
GO
IF DATABASE_PRINCIPAL_ID(N'agent_reader') IS NULL
    CREATE USER agent_reader FOR LOGIN agent_reader;
ELSE
    -- Maps the user to the login again after the database is restored from a backup.
    ALTER USER agent_reader WITH LOGIN = agent_reader;
GO
GRANT EXECUTE ON SCHEMA::api TO agent_reader;
GRANT EXECUTE ON OBJECT::dbo.uspGetBillOfMaterials TO agent_reader;
GRANT EXECUTE ON OBJECT::dbo.uspGetWhereUsedProductID TO agent_reader;
GRANT EXECUTE ON OBJECT::dbo.uspGetEmployeeManagers TO agent_reader;
GRANT EXECUTE ON OBJECT::dbo.uspGetManagerEmployees TO agent_reader;
GO
