-- ─────────────────────────────────────────────────────────────────────
-- exec_sql RPC
-- The agent runs schema introspection (information_schema) and the multi-table
-- aggregation queries the schema map describes through this single read-only
-- RPC. It is SECURITY DEFINER so the service role can execute, and it returns
-- JSON so the Supabase client gets ordinary rows back.
--
-- NOTE: identifiers in every query come from the schema map (never hardcoded),
-- and the agent only issues read queries. If you want hard enforcement, run the
-- agent under a DB role with SELECT-only grants.
-- ─────────────────────────────────────────────────────────────────────
create or replace function public.exec_sql(query text, params jsonb default '{}'::jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    result jsonb;
    cleaned text;
begin
    -- Guard: read-only. Reject anything that isn't a plain SELECT/WITH.
    -- Agents routinely prefix a query with an explanatory SQL comment, so strip
    -- a leading run of whitespace, line comments (-- ... up to newline) and block
    -- comments (/* ... */) before checking. We only normalize for the CHECK; the
    -- original query (comment and all) is what actually executes.
    cleaned := lower(ltrim(
        regexp_replace(query, '^(\s+|--[^\n]*(\n|$)|/\*.*?\*/)+', ''),
        E' \t\n\r'
    ));
    if not (cleaned like 'select%' or cleaned like 'with%') then
        raise exception 'exec_sql only permits read queries';
    end if;

    execute format('select coalesce(jsonb_agg(t), ''[]''::jsonb) from (%s) t', query)
        into result;
    return result;
end;
$$;

-- Restrict execution to the service role used by the agent.
revoke all on function public.exec_sql(text, jsonb) from public;
grant execute on function public.exec_sql(text, jsonb) to service_role;
