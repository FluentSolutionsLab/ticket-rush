-- Ticket Rush schema, Block 1 (see docs/specs/block-1-data-and-performance.md).
-- Indexes beyond primary keys and the unique constraint are left out on purpose:
-- you add them in week 5 and measure the difference.

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

CREATE TABLE IF NOT EXISTS events (
  id            BIGSERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  venue         TEXT NOT NULL,
  starts_at     TIMESTAMPTZ NOT NULL,
  sale_opens_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS seats (
  id          BIGSERIAL PRIMARY KEY,
  event_id    BIGINT NOT NULL REFERENCES events(id),
  section     TEXT NOT NULL,
  row_label   TEXT NOT NULL,
  number      INT  NOT NULL,
  price_cents INT  NOT NULL,
  status      TEXT NOT NULL DEFAULT 'available'
              CHECK (status IN ('available','held','sold')),
  held_by     BIGINT,                  -- booking id
  held_until  TIMESTAMPTZ,
  version     INT NOT NULL DEFAULT 0,  -- optimistic concurrency token
  UNIQUE (event_id, section, row_label, number)
);

CREATE TABLE IF NOT EXISTS bookings (
  id              BIGSERIAL PRIMARY KEY,
  event_id        BIGINT NOT NULL REFERENCES events(id),
  user_id         BIGINT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('held','confirmed','expired','cancelled')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at      TIMESTAMPTZ NOT NULL,
  idempotency_key TEXT
);

CREATE TABLE IF NOT EXISTS booking_seats (
  booking_id BIGINT NOT NULL REFERENCES bookings(id),
  seat_id    BIGINT NOT NULL REFERENCES seats(id),
  PRIMARY KEY (booking_id, seat_id)
);
