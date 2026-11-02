DROP TABLE IF EXISTS payments, accounts;

-- No CHECK constraint: the "no overdraft" rule is enforced by the application.
CREATE TABLE accounts (
    id      INT PRIMARY KEY,
    owner   TEXT NOT NULL,
    balance BIGINT NOT NULL  -- paise
);
