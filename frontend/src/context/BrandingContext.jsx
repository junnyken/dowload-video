import { createContext, useContext, useEffect, useState } from 'react';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const DEFAULT_BRANDING = {
  app_name: 'VidGrab',
  logo_url: null,
  favicon_url: null,
  primary_color: '#6366f1',
  accent_color: '#8b5cf6',
  support_email: 'support@vidgrab.dev',
  hide_powered_by: false,
  is_white_label: false,
};

const BrandingContext = createContext(DEFAULT_BRANDING);

function applyBrandingToDOM(branding) {
  // Apply CSS custom properties
  document.documentElement.style.setProperty('--color-primary', branding.primary_color);
  document.documentElement.style.setProperty('--color-accent', branding.accent_color);

  // Update document title
  if (branding.app_name) {
    document.title = branding.app_name;
  }

  // Update favicon
  if (branding.favicon_url) {
    let link = document.querySelector("link[rel~='icon']");
    if (!link) {
      link = document.createElement('link');
      link.rel = 'icon';
      document.head.appendChild(link);
    }
    link.href = branding.favicon_url;
  }
}

export function BrandingProvider({ children }) {
  const [branding, setBranding] = useState(DEFAULT_BRANDING);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchBranding() {
      try {
        const res = await fetch(`${API_BASE}/api/v1/tenant/branding`);
        if (!res.ok) {
          throw new Error(`Branding fetch failed: ${res.status}`);
        }
        const data = await res.json();
        if (cancelled) return;

        // If not white-label, keep defaults but merge safe fields
        const resolved = data.is_white_label
          ? { ...DEFAULT_BRANDING, ...data }
          : DEFAULT_BRANDING;

        setBranding(resolved);
        applyBrandingToDOM(resolved);
      } catch (err) {
        if (cancelled) return;
        // Fall back to defaults silently on any error
        setError(err.message);
        applyBrandingToDOM(DEFAULT_BRANDING);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    // Apply defaults immediately so DOM is always styled
    applyBrandingToDOM(DEFAULT_BRANDING);
    fetchBranding();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <BrandingContext.Provider value={{ branding, loading, error }}>
      {children}
    </BrandingContext.Provider>
  );
}

export function useBranding() {
  return useContext(BrandingContext);
}
