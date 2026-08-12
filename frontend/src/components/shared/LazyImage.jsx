import { useEffect, useRef, useState } from 'react';

/**
 * LazyImage — loads the src only when the element scrolls into the viewport.
 * Uses IntersectionObserver; falls back to eager load on unsupported browsers.
 *
 * Props: standard <img> props plus:
 *   placeholderClass  {string}  Tailwind classes for the placeholder skeleton
 *   fallback          {string}  src to show on error (default: blank)
 */
export default function LazyImage({
  src,
  alt = '',
  className = '',
  placeholderClass = 'bg-slate-800',
  fallback = '',
  ...rest
}) {
  const ref = useRef(null);
  const [loaded, setLoaded] = useState(false);
  const [active, setActive] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!src) return;
    if (!('IntersectionObserver' in window)) {
      setActive(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setActive(true);
          observer.disconnect();
        }
      },
      { rootMargin: '200px' }
    );
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, [src]);

  return (
    <div ref={ref} className={`relative overflow-hidden ${className}`} {...rest}>
      {/* Skeleton shown until image loads */}
      {!loaded && (
        <div className={`absolute inset-0 animate-pulse ${placeholderClass}`} />
      )}
      {active && src && (
        <img
          src={error ? fallback : src}
          alt={alt}
          loading="lazy"
          decoding="async"
          onLoad={() => setLoaded(true)}
          onError={() => { setError(true); setLoaded(true); }}
          className={`w-full h-full object-cover transition-opacity duration-300 ${loaded ? 'opacity-100' : 'opacity-0'}`}
        />
      )}
    </div>
  );
}
