-- =========================================================
-- Finova / pfe_bd - Full schema recreation script
-- =========================================================

CREATE DATABASE IF NOT EXISTS pfe_bd;
USE pfe_bd;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS rapports;
DROP TABLE IF EXISTS audit_logs;
DROP TABLE IF EXISTS historique_imports;
DROP TABLE IF EXISTS previsions;
DROP TABLE IF EXISTS valeur_kpi;
DROP TABLE IF EXISTS login_history;
DROP TABLE IF EXISTS transactions;
DROP TABLE IF EXISTS projet;
DROP TABLE IF EXISTS clientfournisseur;
DROP TABLE IF EXISTS responsable;
DROP TABLE IF EXISTS typedepense;
DROP TABLE IF EXISTS typetransaction;
DROP TABLE IF EXISTS departement;
DROP TABLE IF EXISTS `date`;
DROP TABLE IF EXISTS users;
SET FOREIGN_KEY_CHECKS = 1;

-- 1) users
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    firstname VARCHAR(100),
    lastname VARCHAR(100),
    email VARCHAR(150) UNIQUE NOT NULL,
    password VARCHAR(255),
    role VARCHAR(50) DEFAULT 'user',
    login_type VARCHAR(50) DEFAULT 'email',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2) date
CREATE TABLE `date` (
    Date_ID INT AUTO_INCREMENT PRIMARY KEY,
    `Date` DATE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3) departement
CREATE TABLE departement (
    Departement_ID INT AUTO_INCREMENT PRIMARY KEY,
    NomDepartement VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4) typetransaction
CREATE TABLE typetransaction (
    TypeTransaction_ID INT AUTO_INCREMENT PRIMARY KEY,
    TypeTransaction VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5) typedepense
CREATE TABLE typedepense (
    TypeDepense_ID INT AUTO_INCREMENT PRIMARY KEY,
    TypeDepense VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 6) responsable
CREATE TABLE responsable (
    Responsable_ID INT AUTO_INCREMENT PRIMARY KEY,
    NomResponsable VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 7) clientfournisseur
CREATE TABLE clientfournisseur (
    ClientFournisseur_ID INT AUTO_INCREMENT PRIMARY KEY,
    NomClientFournisseur VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 8) projet
CREATE TABLE projet (
    Projet_ID INT AUTO_INCREMENT PRIMARY KEY,
    NomProjet VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 9) transactions
CREATE TABLE transactions (
    Transaction_ID INT AUTO_INCREMENT PRIMARY KEY,
    Montant DECIMAL(15,3),
    Montant_Signe DECIMAL(15,3),
    Date_ID INT NULL,
    Departement_ID INT NULL,
    TypeTransaction_ID INT NULL,
    TypeDepense_ID INT NULL,
    Responsable_ID INT NULL,
    ClientFournisseur_ID INT NULL,
    Projet_ID INT NULL,
    year_month VARCHAR(10),
    departement VARCHAR(255),
    type_transaction VARCHAR(255),
    type_depense VARCHAR(255),
    responsable VARCHAR(255),
    client_fournisseur VARCHAR(255),
    cf_type VARCHAR(50),
    projet VARCHAR(255),
    CONSTRAINT fk_transactions_date
        FOREIGN KEY (Date_ID) REFERENCES `date`(Date_ID) ON DELETE SET NULL,
    CONSTRAINT fk_transactions_departement
        FOREIGN KEY (Departement_ID) REFERENCES departement(Departement_ID) ON DELETE SET NULL,
    CONSTRAINT fk_transactions_typetransaction
        FOREIGN KEY (TypeTransaction_ID) REFERENCES typetransaction(TypeTransaction_ID) ON DELETE SET NULL,
    CONSTRAINT fk_transactions_typedepense
        FOREIGN KEY (TypeDepense_ID) REFERENCES typedepense(TypeDepense_ID) ON DELETE SET NULL,
    CONSTRAINT fk_transactions_responsable
        FOREIGN KEY (Responsable_ID) REFERENCES responsable(Responsable_ID) ON DELETE SET NULL,
    CONSTRAINT fk_transactions_clientfournisseur
        FOREIGN KEY (ClientFournisseur_ID) REFERENCES clientfournisseur(ClientFournisseur_ID) ON DELETE SET NULL,
    CONSTRAINT fk_transactions_projet
        FOREIGN KEY (Projet_ID) REFERENCES projet(Projet_ID) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 10) login_history
CREATE TABLE login_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NULL,
    email VARCHAR(255) NOT NULL,
    login_status VARCHAR(50) NOT NULL,
    login_type VARCHAR(50) NOT NULL DEFAULT 'email',
    ip_address VARCHAR(100) NULL,
    user_agent TEXT NULL,
    login_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    failure_reason VARCHAR(255) NULL,
    CONSTRAINT fk_login_history_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 11) valeur_kpi
CREATE TABLE valeur_kpi (
    id INT AUTO_INCREMENT PRIMARY KEY,
    kpiNom VARCHAR(255) NOT NULL,
    periode VARCHAR(50) NOT NULL,
    valeur FLOAT NOT NULL,
    evolution FLOAT DEFAULT 0,
    departementId INT DEFAULT NULL,
    source VARCHAR(255) DEFAULT 'etl',
    stat_type VARCHAR(20) DEFAULT 'sum',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 12) previsions
CREATE TABLE previsions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    type VARCHAR(100) NOT NULL,
    dateDebut DATE NOT NULL,
    dateFin DATE NOT NULL,
    resultats JSON NOT NULL,
    departementId INT DEFAULT NULL,
    created_by INT DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_previsions_created_by
        FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 13) historique_imports
CREATE TABLE historique_imports (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT DEFAULT NULL,
    nom_fichier VARCHAR(255) NOT NULL,
    date_import DATETIME DEFAULT CURRENT_TIMESTAMP,
    nb_lignes INT DEFAULT 0,
    nb_erreurs INT DEFAULT 0,
    statut VARCHAR(20) NOT NULL DEFAULT 'succes',
    departement VARCHAR(255) DEFAULT NULL,
    importe_par VARCHAR(255) DEFAULT NULL,
    details LONGTEXT DEFAULT NULL,
    data LONGTEXT DEFAULT NULL,
    CONSTRAINT fk_historique_imports_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 14) audit_logs
CREATE TABLE audit_logs (
    id_log INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NULL,
    action VARCHAR(100) NOT NULL,
    ressource VARCHAR(150) NULL,
    statut VARCHAR(30) DEFAULT 'success',
    ip_address VARCHAR(100) NULL,
    details TEXT NULL,
    date_action DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_audit_logs_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 15) rapports
CREATE TABLE rapports (
    id_rapport INT AUTO_INCREMENT PRIMARY KEY,
    nom_rapport VARCHAR(255) NOT NULL,
    type_rapport VARCHAR(100) DEFAULT 'financier',
    format VARCHAR(20) NOT NULL,
    chemin_fichier VARCHAR(500) NULL,
    created_by INT NULL,
    date_generation DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_rapports_created_by
        FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =========================================================
-- Performance indexes
-- =========================================================

-- login_history
CREATE INDEX idx_login_history_date_id ON login_history(login_date, id);
CREATE INDEX idx_login_history_email ON login_history(email);
CREATE INDEX idx_login_history_status ON login_history(login_status);
CREATE INDEX idx_login_history_user_id ON login_history(user_id);

-- audit_logs
CREATE INDEX idx_audit_logs_date_id ON audit_logs(date_action, id_log);
CREATE INDEX idx_audit_logs_action ON audit_logs(action);
CREATE INDEX idx_audit_logs_statut ON audit_logs(statut);
CREATE INDEX idx_audit_logs_user_id ON audit_logs(user_id);

-- rapports
CREATE INDEX idx_rapports_created_by ON rapports(created_by);
CREATE INDEX idx_rapports_date_generation ON rapports(date_generation);
CREATE INDEX idx_rapports_format ON rapports(format);

-- transactions
CREATE INDEX idx_transactions_date_id ON transactions(Date_ID);
CREATE INDEX idx_transactions_departement_id ON transactions(Departement_ID);
CREATE INDEX idx_transactions_date_departement ON transactions(Date_ID, Departement_ID);
CREATE INDEX idx_transactions_montant_signe ON transactions(Montant_Signe);
CREATE INDEX idx_transactions_transaction_id ON transactions(Transaction_ID);

-- valeur_kpi
CREATE INDEX idx_valeur_kpi_nom ON valeur_kpi(kpiNom);
CREATE INDEX idx_valeur_kpi_periode ON valeur_kpi(periode);
CREATE INDEX idx_valeur_kpi_updated_at ON valeur_kpi(updated_at);
CREATE INDEX idx_valeur_kpi_created_at ON valeur_kpi(created_at);

-- date
CREATE INDEX idx_date_date ON `date`(`Date`);
CREATE INDEX idx_date_date_id ON `date`(Date_ID);

-- departement
CREATE INDEX idx_departement_id ON departement(Departement_ID);
CREATE INDEX idx_departement_nom ON departement(NomDepartement);
