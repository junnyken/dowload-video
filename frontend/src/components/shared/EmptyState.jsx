/**
 * Reusable empty state component used across Archive, Schedule, History,
 * Collections, and Notes views.
 *
 * Props:
 *   icon     {ReactNode}   visual icon (lucide or emoji string)
 *   title    {string}      short headline
 *   body     {string}      one-sentence instruction — what to do next
 *   action   {ReactNode}   optional CTA button
 *   compact  {boolean}     reduced padding variant for inline use
 */
export default function EmptyState({ icon, title, body, action, compact = false }) {
  return (
    <div className={`flex flex-col items-center justify-center text-center ${compact ? 'py-8 gap-2' : 'py-16 gap-3'}`}>
      {icon && (
        <div className={`${compact ? 'text-3xl' : 'text-5xl'} opacity-50 select-none`}>
          {typeof icon === 'string' ? (
            <span>{icon}</span>
          ) : (
            <span className="text-slate-600">{icon}</span>
          )}
        </div>
      )}
      {title && (
        <p className={`font-bold text-slate-300 ${compact ? 'text-sm' : 'text-base'}`}>
          {title}
        </p>
      )}
      {body && (
        <p className={`text-slate-500 max-w-xs leading-relaxed ${compact ? 'text-xs' : 'text-sm'}`}>
          {body}
        </p>
      )}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}
