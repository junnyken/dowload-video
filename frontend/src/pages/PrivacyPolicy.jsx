export default function PrivacyPolicy() {
  return (
    <div className="text-slate-100">
      {/* Header */}
      <div className="mb-10 border-b border-slate-700/50 pb-6">
        <h1 className="text-3xl font-extrabold text-white mb-2">Privacy Policy</h1>
        <p className="text-slate-400 text-sm">
          Chính sách Bảo mật — <span className="text-slate-300">Last updated: June 2026</span>
        </p>
      </div>

      <div className="space-y-10 text-slate-300 leading-relaxed">

        {/* 1 — Data collected */}
        <section>
          <h2 className="text-xl font-bold text-white mb-3">
            1. Dữ liệu chúng tôi thu thập / Data We Collect
          </h2>
          <p className="mb-3">
            VidGrab chỉ thu thập dữ liệu tối thiểu cần thiết để vận hành dịch vụ:
          </p>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <li>
              <span className="text-white font-medium">Email &amp; thông tin tài khoản</span> — được cung cấp khi bạn đăng ký hoặc đăng nhập (qua Supabase Auth). Dùng để xác thực và liên lạc.
            </li>
            <li>
              <span className="text-white font-medium">Lịch sử tải xuống</span> — URL video bạn đã tải, thời gian, định dạng chọn. Lưu trong tài khoản để tiện tra cứu lại.
            </li>
            <li>
              <span className="text-white font-medium">Tùy chọn cá nhân</span> — ngôn ngữ, định dạng mặc định, chế độ tối/sáng.
            </li>
            <li>
              <span className="text-white font-medium">Log kỹ thuật</span> — IP ẩn danh, loại trình duyệt, lỗi phát sinh — chỉ dùng để debug và bảo mật hệ thống.
            </li>
          </ul>
          <p className="mt-4 text-slate-400 text-sm italic">
            We collect only: account email, download history (URLs + formats), preferences, and anonymised technical logs for debugging.
          </p>
        </section>

        {/* 2 — Data NOT collected */}
        <section>
          <h2 className="text-xl font-bold text-white mb-3">
            2. Dữ liệu chúng tôi KHÔNG thu thập / What We Do NOT Collect
          </h2>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <li>Nội dung video (file video không lưu trên server của chúng tôi — chỉ chuyển thẳng đến bạn)</li>
            <li>Số thẻ tín dụng / thông tin thanh toán lưu trữ phía server (Stripe xử lý và lưu trực tiếp)</li>
            <li>Dữ liệu sinh trắc học, dữ liệu sức khỏe, hoặc dữ liệu nhạy cảm bất kỳ</li>
            <li>Danh sách liên lạc, tin nhắn hoặc bất kỳ dữ liệu từ thiết bị của bạn</li>
          </ul>
          <p className="mt-4 text-slate-400 text-sm italic">
            Video content is never stored on our servers — we only broker the download link. Payment card data is handled exclusively by Stripe and never stored by VidGrab.
          </p>
        </section>

        {/* 3 — Data retention */}
        <section>
          <h2 className="text-xl font-bold text-white mb-3">
            3. Thời gian lưu trữ dữ liệu / Data Retention
          </h2>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <li>
              <span className="text-white font-medium">Link tải xuống</span> — hết hạn sau <span className="text-[#FBBF24] font-semibold">60 phút</span> kể từ khi tạo.
            </li>
            <li>
              <span className="text-white font-medium">Lịch sử tải</span> — lưu đến khi bạn xóa thủ công trong phần "Lịch sử" của tài khoản.
            </li>
            <li>
              <span className="text-white font-medium">Tài khoản</span> — dữ liệu tài khoản được giữ cho đến khi bạn yêu cầu xóa tài khoản qua email hỗ trợ.
            </li>
            <li>
              <span className="text-white font-medium">Log kỹ thuật</span> — tự động xóa sau 30 ngày.
            </li>
          </ul>
          <p className="mt-4 text-slate-400 text-sm italic">
            Download links expire in 60 minutes. Download history is retained until you delete it. Technical logs auto-purge after 30 days.
          </p>
        </section>

        {/* 4 — Third parties */}
        <section>
          <h2 className="text-xl font-bold text-white mb-3">
            4. Bên thứ ba / Third-Party Services
          </h2>
          <p className="mb-4">
            VidGrab sử dụng các dịch vụ bên thứ ba đáng tin cậy sau:
          </p>
          <div className="space-y-4">
            <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/40">
              <p className="font-bold text-white mb-1">Supabase</p>
              <p className="text-sm">Xác thực người dùng (Auth) và lưu trữ cơ sở dữ liệu. Tuân thủ GDPR và SOC 2 Type II. Dữ liệu lưu tại EU/US theo lựa chọn project.</p>
            </div>
            <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/40">
              <p className="font-bold text-white mb-1">Stripe</p>
              <p className="text-sm">Xử lý thanh toán an toàn (PCI DSS Level 1). Thông tin thẻ không bao giờ đi qua hoặc lưu trên server VidGrab.</p>
            </div>
          </div>
          <p className="mt-4 font-semibold text-red-400">
            Chúng tôi KHÔNG bán, cho thuê hoặc chia sẻ dữ liệu của bạn cho bất kỳ bên thứ ba nào cho mục đích quảng cáo.
          </p>
          <p className="mt-2 text-slate-400 text-sm italic">
            We use Supabase (auth/DB) and Stripe (payments). We do NOT sell or share your data with any advertising or data-broker third parties.
          </p>
        </section>

        {/* 5 — Chrome Extension */}
        <section>
          <h2 className="text-xl font-bold text-white mb-3">
            5. Tiện ích Chrome / Chrome Extension
          </h2>
          <p className="mb-3">
            Tiện ích VidGrab Chrome yêu cầu một số quyền để hoạt động:
          </p>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <li><span className="text-white font-medium">activeTab / scripting</span> — đọc URL trang hiện tại để phát hiện video, không đọc nội dung trang hay cookie.</li>
            <li><span className="text-white font-medium">downloads</span> — kích hoạt tải file về máy bạn qua Chrome API.</li>
            <li><span className="text-white font-medium">storage</span> — lưu tùy chọn cục bộ (token, cài đặt) trong trình duyệt của bạn.</li>
            <li><span className="text-white font-medium">contextMenus</span> — thêm menu chuột phải "Tải với VidGrab" trên link video.</li>
          </ul>
          <p className="mt-4 text-slate-400 text-sm italic">
            The extension only reads the current page URL to detect downloadable videos. It does not read page content, cookies, or form data beyond what is necessary for video detection.
          </p>
        </section>

        {/* 6 — Contact */}
        <section>
          <h2 className="text-xl font-bold text-white mb-3">
            6. Liên hệ / Contact
          </h2>
          <p className="mb-2">
            Nếu bạn có câu hỏi về chính sách bảo mật hoặc muốn yêu cầu xóa dữ liệu, vui lòng liên hệ:
          </p>
          <p className="mb-4 text-slate-400 text-sm italic">
            For privacy questions or data deletion requests, please contact us at:
          </p>
          <a
            href="mailto:privacy@vidgrab.io"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-[#FBBF24]/10 border border-[#FBBF24]/30 text-[#FBBF24] font-semibold hover:bg-[#FBBF24]/20 transition-colors"
          >
            privacy@vidgrab.io
          </a>
          <p className="mt-4 text-slate-500 text-sm">
            Chúng tôi cam kết phản hồi trong vòng 5 ngày làm việc. / We aim to respond within 5 business days.
          </p>
        </section>

      </div>
    </div>
  );
}
