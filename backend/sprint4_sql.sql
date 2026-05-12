USE pfe_bd;

-- US18 / US19
CREATE TABLE IF NOT EXISTS rapports (
    id_rapport INT AUTO_INCREMENT PRIMARY KEY,
    nom_rapport VARCHAR(255) NOT NULL,
    type_rapport VARCHAR(100) DEFAULT 'financier',
    format VARCHAR(20) NOT NULL,
    chemin_fichier VARCHAR(500) NULL,
    created_by INT NULL,
    date_generation DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- US23
CREATE TABLE IF NOT EXISTS audit_logs (
    id_log INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NULL,
    action VARCHAR(100) NOT NULL,
    ressource VARCHAR(150) NULL,
    statut VARCHAR(30) DEFAULT 'success',
    ip_address VARCHAR(100) NULL,
    details TEXT NULL,
    date_action DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Index performance (ignore duplicate index error if already present)
CREATE INDEX idx_rapports_created_by ON rapports(created_by);
CREATE INDEX idx_rapports_date ON rapports(date_generation);
CREATE INDEX idx_rapports_format ON rapports(format);

CREATE INDEX idx_audit_user ON audit_logs(user_id);
CREATE INDEX idx_audit_action ON audit_logs(action);
CREATE INDEX idx_audit_statut ON audit_logs(statut);
CREATE INDEX idx_audit_date ON audit_logs(date_action);

CREATE INDEX idx_hist_user_status_date ON historique_imports(user_id, statut, date_import, id);

CREATE INDEX idx_vkpi_nom ON valeur_kpi(kpiNom);
CREATE INDEX idx_vkpi_periode ON valeur_kpi(periode);
CREATE INDEX idx_vkpi_updated_at ON valeur_kpi(updated_at);

CREATE INDEX idx_tx_date_id ON transactions(Date_ID);
CREATE INDEX idx_tx_departement_id ON transactions(Departement_ID);
CREATE INDEX idx_tx_date_dep ON transactions(Date_ID, Departement_ID);
CREATE INDEX idx_tx_montant_signe ON transactions(Montant_Signe);

CREATE INDEX idx_date_value ON `date`(`Date`);
CREATE INDEX idx_dep_name ON departement(NomDepartement);

-- Données de test
SET @test_user := (SELECT id FROM users ORDER BY id LIMIT 1);

INSERT INTO rapports (nom_rapport, type_rapport, format, chemin_fichier, created_by, date_generation)
VALUES
('Rapport mensuel KPI', 'financier', 'pdf', 'demo_rapport_kpi.pdf', @test_user, NOW()),
('Synthèse dépenses', 'financier', 'png', 'demo_depenses.png', @test_user, NOW() - INTERVAL 1 DAY),
('Export transactions', 'financier', 'csv', 'demo_transactions.csv', @test_user, NOW() - INTERVAL 2 DAY);

INSERT INTO audit_logs (user_id, action, ressource, statut, ip_address, details, date_action)
VALUES
(@test_user, 'connexion', 'auth_email', 'success', '127.0.0.1', 'LOGIN_SUCCESS', NOW()),
(@test_user, 'connexion', 'auth_email', 'failed', '127.0.0.1', 'LOGIN_FAILED', NOW() - INTERVAL 5 MINUTE),
(@test_user, 'creation_utilisateur', 'users', 'success', '127.0.0.1', 'USER_CREATED', NOW() - INTERVAL 10 MINUTE),
(@test_user, 'modification_utilisateur', 'users', 'success', '127.0.0.1', 'USER_UPDATED', NOW() - INTERVAL 15 MINUTE),
(@test_user, 'changement_role', 'users', 'success', '127.0.0.1', 'ROLE_CHANGED', NOW() - INTERVAL 20 MINUTE),
(@test_user, 'suppression_utilisateur', 'users', 'failed', '127.0.0.1', 'USER_DELETED', NOW() - INTERVAL 25 MINUTE),
(@test_user, 'import_donnees', 'etl_process', 'success', '127.0.0.1', 'ETL_SUCCESS', NOW() - INTERVAL 30 MINUTE),
(@test_user, 'import_donnees', 'etl_process', 'failed', '127.0.0.1', 'ETL_FAILED', NOW() - INTERVAL 35 MINUTE),
(@test_user, 'generation_rapport', 'reports_export', 'success', '127.0.0.1', 'REPORT_GENERATED', NOW() - INTERVAL 40 MINUTE),
(@test_user, 'export_rapport', 'report_download', 'success', '127.0.0.1', 'REPORT_EXPORTED', NOW() - INTERVAL 45 MINUTE),
(@test_user, 'api_access', 'dashboard_summary', 'success', '127.0.0.1', 'API_ACCESS', NOW() - INTERVAL 50 MINUTE),
(@test_user, 'erreur_systeme', 'dashboard_summary', 'failed', '127.0.0.1', 'SYSTEM_ERROR', NOW() - INTERVAL 55 MINUTE);
