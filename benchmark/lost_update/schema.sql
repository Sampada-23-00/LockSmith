DROP TABLE IF EXISTS payments, withdrawals, accounts;

CREATE TABLE accounts (
    id      INT PRIMARY KEY,
    owner   TEXT NOT NULL,
    balance BIGINT NOT NULL CHECK (balance >= 0)  -- paise
);
