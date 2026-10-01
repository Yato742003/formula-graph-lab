/// <reference types="vite/client" />

declare namespace Cloudflare {
  interface Env {
    APP_ENV?: string;
    FGL_TRUST_SITES_IDENTITY?: string;
    DB: D1Database;
    GRAPH_API_SERVICE_TOKEN?: string;
    GRAPH_API_URL?: string;
  }
}
