// QuotaBar — reusable quota progress bar
// Props: used, limit (-1 = unlimited), label, unit, size ('sm' | 'md')

export default function QuotaBar({ used = 0, limit = -1, label = '', unit = '', size = 'md' }) {
  const unlimited = limit === -1;
  const pct       = unlimited ? 100 : limit > 0 ? Math.min(Math.round((used / limit) * 100), 100) : 0;

  let barColor = 'bg-emerald-500';
  if (!unlimited) {
    if (pct >= 100) barColor = 'bg-red-500';
    else if (pct >= 80) barColor = 'bg-yellow-500';
  }

  const isSm = size === 'sm';

  const countText = unlimited
    ? `${used} / ∞${unit ? ' ' + unit : ''}`
    : `${used} / ${limit}${unit ? ' ' + unit : ''}`;

  const subText = unlimited ? 'Không giới hạn' : null;

  return (
    <div className={isSm ? 'space-y-0.5' : 'space-y-1.5'}>
      <div className={`flex items-center justify-between ${isSm ? 'text-xs' : 'text-sm'}`}>
        <span className={isSm ? 'text-zinc-400' : 'text-zinc-300 font-medium'}>{label}</span>
        <span className="text-zinc-400">
          {unlimited ? (
            <span className="text-emerald-400 font-medium">Không giới hạn</span>
          ) : (
            countText
          )}
        </span>
      </div>

      <div
        className={`w-full rounded-full bg-zinc-700/60 overflow-hidden ${isSm ? 'h-1' : 'h-2'}`}
        role="progressbar"
        aria-valuenow={unlimited ? 100 : pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
      >
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      {!isSm && subText && (
        <p className="text-xs text-zinc-500">{subText}</p>
      )}
    </div>
  );
}
