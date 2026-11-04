DROP TABLE IF EXISTS payments, withdrawals, accounts;

-- Accounts 1+2, 3+4, ... belong to the same household.
CREATE TABLE accounts (
    id        INT PRIMARY KEY,
    owner     TEXT NOT NULL,
    balance   BIGINT NOT NULL,  -- paise
    household INT GENERATED ALWAYS AS ((id + 1) / 2) STORED
);
