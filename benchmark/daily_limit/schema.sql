DROP TABLE IF EXISTS payments, withdrawals, accounts;

CREATE TABLE accounts (
    id      INT PRIMARY KEY,
    owner   TEXT NOT NULL,
    balance BIGINT NOT NULL CHECK (balance >= 0)  -- paise
);

CREATE TABLE withdrawals (
    id         BIGSERIAL PRIMARY KEY,
    account_id INT NOT NULL REFERENCES accounts (id),
    amount     BIGINT NOT NULL,
    day        DATE NOT NULL DEFAULT current_date
);

CREATE INDEX withdrawals_account_day_idx ON withdrawals (account_id, day);
