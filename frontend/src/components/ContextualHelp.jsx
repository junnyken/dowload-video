import { useState, useEffect, useRef } from "react";

const HELP_TOPICS = {
  quality_selector:    "Chọn chất lượng video. 4K cần nhiều thời gian hơn và dung lượng lớn hơn. Dùng 'video_fast' nếu cần tải nhanh.",
  batch_download:      "Tải nhiều video cùng lúc theo kênh hoặc playlist. Mỗi đợt tối đa 10 video để tránh bị chặn.",
  schedule:            "Lên lịch tải video tự động theo cron expression. Ví dụ: '0 9 * * *' = mỗi ngày 9h sáng.",
  archive_pin:         "Ghim video vào archive để không bị xóa sau 24 giờ. File sẽ được giữ lại vĩnh viễn (hoặc đến khi bạn xóa).",
  remove_watermark:    "Xóa watermark bằng AI inpainting. Chỉ hỗ trợ TikTok/Douyin. Mất thêm 30–60 giây để xử lý.",
  webhook:             "Nhận thông báo khi tải xong qua URL webhook của bạn. Gửi POST request với thông tin job.",
  approval_workflow:   "Tải xuống quy mô lớn cần admin workspace duyệt trước khi thực hiện. Bạn sẽ nhận thông báo khi được duyệt.",
  auto_retry:          "Hệ thống tự động thử lại khi gặp lỗi tạm thời (rate limit, timeout). Tối đa 3–4 lần với backoff.",
};

export function ContextualHelp({ topic, position = "right" }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const text = HELP_TOPICS[topic];

  useEffect(() => {
    if (!open) return;
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  if (!text) return null;

  const posClass = {
    right:  "left-6 top-0",
    top:    "bottom-6 left-1/2 -translate-x-1/2",
    bottom: "top-6 left-1/2 -translate-x-1/2",
  }[position] || "left-6 top-0";

  return (
    <span ref={ref} className="relative inline-flex items-center">
      <button
        onClick={() => setOpen(!open)}
        className="w-4 h-4 rounded-full bg-zinc-700 text-zinc-400 hover:bg-zinc-600 hover:text-white text-xs flex items-center justify-center transition ml-1"
        title="Trợ giúp"
      >
        ?
      </button>
      {open && (
        <div className={`absolute z-50 w-64 p-3 bg-zinc-800 border border-zinc-600 rounded-lg shadow-xl text-xs text-zinc-300 leading-relaxed ${posClass}`}>
          {text}
          <div className="absolute w-2 h-2 bg-zinc-800 border-l border-t border-zinc-600 rotate-[-135deg] -left-1 top-3" />
        </div>
      )}
    </span>
  );
}

export function FirstTimeHint({ feature, children }) {
  const [seen, setSeen] = useState(true);

  useEffect(() => {
    try {
      const raw = localStorage.getItem("vidgrab:seen_features");
      const list = raw ? JSON.parse(raw) : [];
      setSeen(list.includes(feature));
    } catch (_) {}
  }, [feature]);

  const markSeen = () => {
    try {
      const raw = localStorage.getItem("vidgrab:seen_features");
      const list = raw ? JSON.parse(raw) : [];
      if (!list.includes(feature)) {
        list.push(feature);
        localStorage.setItem("vidgrab:seen_features", JSON.stringify(list));
      }
    } catch (_) {}
    setSeen(true);
  };

  return (
    <span className="relative inline-flex items-center gap-1" onClick={markSeen}>
      {children}
      {!seen && (
        <span className="absolute -top-1.5 -right-2 px-1.5 py-0.5 text-[9px] font-bold rounded-full bg-blue-500 text-white animate-pulse leading-none">
          Mới
        </span>
      )}
    </span>
  );
}

export default ContextualHelp;
