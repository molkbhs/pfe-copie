-- Migration: Ajout colonnes pour Admin Dashboard
-- Exécuter: mysql -u root pfe_bd < schema_update.sql
USE pfe_bd;

ALTER TABLE users ADD COLUMN login_type VARCHAR(20) DEFAULT 'email';
ALTER TABLE users ADD COLUMN status VARCHAR(20) DEFAULT 'active';
ALTER TABLE users ADD COLUMN last_login TIMESTAMP NULL;

-- Si erreur "Duplicate column", les colonnes existent déjà.
