DROP TABLE IF EXISTS payments, withdrawals, accounts;

CREATE TABLE accounts (
    id      INT PRIMARY KEY,
    owner   TEXT NOT NULL,
    balance BIGINT NOT NULL CHECK (balance >= 0)  -- paise
);

CREATE TABLE payments (
    id              BIGSERIAL PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    account_id      INT NOT NULL REFERENCES accounts (id),
    amount          BIGINT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX payments_idempotency_key_idx ON payments (idempotency_key);
