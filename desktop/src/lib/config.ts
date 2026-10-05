// Public endpoints/keys. Nothing secret lives here.
export const API_BASE = 'https://dvid-api.vibe1.tinhgon.xyz';
export const WEBSITE_URL = 'https://dvid.vibe1.tinhgon.xyz';
export const SUPABASE_URL = 'https://wtwnbagcqyedindtcadv.supabase.co';

// This is the same public (role: anon) key the website ships in its JS bundle
// (https://dvid.vibe1.tinhgon.xyz). It is NOT a secret: access is enforced by
// Supabase Row Level Security and by the user's own JWT.
export const SUPABASE_ANON_KEY =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Ind0d25iYWdjcXllZGluZHRjYWR2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzMwOTA2NjMsImV4cCI6MjA4ODY2NjY2M30.s0fLtsS-EWyeodWwhhdonHNTn9v2l13xcZSJtK9pD80';

export const PROBE_CONCURRENCY = 3;
