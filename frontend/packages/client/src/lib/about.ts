import type { AuthConfig } from "../api/types";

/** Mirror of `SOURCE_URL`'s default (packages/config/src/schema.ts), for a server too old to send `sourceUrl`. */
export const PUBLIC_SOURCE_URL = "";

/**
 * A self-hosted server: never agentes.chat Cloud, nor before the server's config arrives, so the Cloud never shows
 * what is only for self-hosting for a moment.
 */
export const selfHosted = (server: AuthConfig | undefined) => server !== undefined && server.cloud !== true;

/**
 * Whether the About screen shows: on every self-hosted server, for everyone; never on agentes.chat Cloud, which is
 * agentes.chat itself.
 */
export const showsAbout = selfHosted;
