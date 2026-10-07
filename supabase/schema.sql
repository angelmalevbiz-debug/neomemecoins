-- NEO Meme Coins — Supabase Auth + isolated paper-trading accounts
-- Target project: qziuovwcauaklgqscqys
-- Safe to paste in Supabase -> SQL Editor -> New query -> Run

begin;

create schema if not exists private;

-- 1) Public profile, keyed by the real Supabase Auth user UUID.
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  username text not null,
  plan text not null default 'demo'
    check (plan in ('demo', 'beta', 'pro', 'admin')),
  created_at timestamptz not null default now()
);

-- 2) One isolated paper account per user.
-- The GOLD production strategy is intentionally fixed here as well.
--
-- Ledger-of-record note (2026-10-08): paper_accounts and paper_trades are NOT
-- the PAPER ledger of record. Each per-user PAPER engine behind the gateway
-- (VPS or PC) keeps its ledger in files under its runtime directory
-- (users/<uuid>/state.json, audit JSONL, training files), and the shared main
-- account likewise lives in state.json. The Python backend does not write these
-- two tables; the browser holds read-only grants on them. The fixed labels below
-- (ORDER_FLOW_ADAPTIVE, gold-2026-10-04) date from the 2026-10-04 strategy and
-- do not match the engine's published WINNER_ENSEMBLE_PAPER_V1 /
-- WINNER_ENSEMBLE_VERIFIED_ENTRY_V4 labels. Changing the check constraints or
-- defaults is an owner decision; this comment only records the mismatch.
create table if not exists public.paper_accounts (
  user_id uuid primary key references auth.users(id) on delete cascade,
  strategy_id text not null default 'ORDER_FLOW_ADAPTIVE'
    check (strategy_id = 'ORDER_FLOW_ADAPTIVE'),
  strategy_version text not null default 'gold-2026-10-04',
  starting_balance_usd numeric(18,2) not null default 1000.00
    check (starting_balance_usd >= 0),
  balance_usd numeric(18,2) not null default 1000.00
    check (balance_usd >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 3) User-specific paper-trade journal.
-- Browser users can read only their own rows. Writes are backend/service-role only;
-- no current backend writer exists (see the ledger-of-record note above).
create table if not exists public.paper_trades (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  strategy_id text not null default 'ORDER_FLOW_ADAPTIVE'
    check (strategy_id = 'ORDER_FLOW_ADAPTIVE'),
  strategy_version text not null default 'gold-2026-10-04',
  token_address text not null,
  token_symbol text not null,
  side text not null check (side in ('BUY', 'SELL')),
  status text not null default 'OPEN'
    check (status in ('OPEN', 'CLOSED', 'CANCELLED')),
  entry_price numeric,
  exit_price numeric,
  notional_usd numeric(18,2) not null
    check (notional_usd > 0),
  pnl_usd numeric(18,6),
  pnl_pct numeric(18,6),
  exit_reason text,
  opened_at timestamptz not null default now(),
  closed_at timestamptz
);

create index if not exists paper_trades_user_opened_idx
  on public.paper_trades (user_id, opened_at desc);

-- 4) Row Level Security.
alter table public.profiles enable row level security;
alter table public.paper_accounts enable row level security;
alter table public.paper_trades enable row level security;

-- Remove accidental/default client privileges first.
revoke all on public.profiles from anon, authenticated;
revoke all on public.paper_accounts from anon, authenticated;
revoke all on public.paper_trades from anon, authenticated;

-- Signed-in users can only read. They cannot change plan, strategy, balances or trades.
grant select on public.profiles to authenticated;
grant select on public.paper_accounts to authenticated;
grant select on public.paper_trades to authenticated;

-- Backend service role retains full access.
grant all on public.profiles to service_role;
grant all on public.paper_accounts to service_role;
grant all on public.paper_trades to service_role;

drop policy if exists "profiles_read_own" on public.profiles;
create policy "profiles_read_own"
on public.profiles
for select
to authenticated
using ((select auth.uid()) = id);

drop policy if exists "paper_accounts_read_own" on public.paper_accounts;
create policy "paper_accounts_read_own"
on public.paper_accounts
for select
to authenticated
using ((select auth.uid()) = user_id);

drop policy if exists "paper_trades_read_own" on public.paper_trades;
create policy "paper_trades_read_own"
on public.paper_trades
for select
to authenticated
using ((select auth.uid()) = user_id);

-- 5) Auth trigger. Runs with postgres privileges so signup can create
-- profile + paper account without exposing insert rights to the browser.
create or replace function private.handle_new_neo_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  display_username text;
begin
  display_username := coalesce(
    nullif(trim(new.raw_user_meta_data ->> 'username'), ''),
    nullif(split_part(coalesce(new.email, ''), '@', 1), ''),
    'neo-' || left(new.id::text, 8)
  );

  insert into public.profiles (id, username, plan, created_at)
  values (
    new.id,
    display_username,
    'demo',
    coalesce(new.created_at, now())
  )
  on conflict (id) do nothing;

  insert into public.paper_accounts (
    user_id,
    strategy_id,
    strategy_version,
    starting_balance_usd,
    balance_usd,
    created_at,
    updated_at
  )
  values (
    new.id,
    'ORDER_FLOW_ADAPTIVE',
    'gold-2026-10-04',
    1000.00,
    1000.00,
    coalesce(new.created_at, now()),
    now()
  )
  on conflict (user_id) do nothing;

  return new;
end;
$$;

revoke all on function private.handle_new_neo_user() from public, anon, authenticated;

drop trigger if exists on_auth_user_created_neo on auth.users;
create trigger on_auth_user_created_neo
  after insert on auth.users
  for each row
  execute function private.handle_new_neo_user();

-- 6) Backfill in case test users already exist.
insert into public.profiles (id, username, plan, created_at)
select
  u.id,
  coalesce(
    nullif(trim(u.raw_user_meta_data ->> 'username'), ''),
    nullif(split_part(coalesce(u.email, ''), '@', 1), ''),
    'neo-' || left(u.id::text, 8)
  ),
  'demo',
  u.created_at
from auth.users u
on conflict (id) do nothing;

insert into public.paper_accounts (
  user_id,
  strategy_id,
  strategy_version,
  starting_balance_usd,
  balance_usd,
  created_at,
  updated_at
)
select
  u.id,
  'ORDER_FLOW_ADAPTIVE',
  'gold-2026-10-04',
  1000.00,
  1000.00,
  u.created_at,
  now()
from auth.users u
on conflict (user_id) do nothing;

commit;

-- Verification queries:
select id, username, plan, created_at
from public.profiles
order by created_at desc;

select user_id, strategy_id, strategy_version, starting_balance_usd, balance_usd
from public.paper_accounts
order by created_at desc;
