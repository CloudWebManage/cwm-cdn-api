create table tenants (
    id text not null primary key
);

create index idx_tenants_id on tenants (id);

create table tenant_domains (
    tenant_id text not null,
    domain text not null,
    cert text,
    key text
);

create index idx_tenant_domains_tenant_id_domain on tenant_domains (tenant_id, domain);

create table tenant_origins (
    tenant_id text not null,
    origin_url text not null
);

create index idx_tenant_origins_tenant_id_origin_url on tenant_origins (tenant_id, origin_url);
