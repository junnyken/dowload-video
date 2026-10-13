import { SUPPORT_EMAIL } from '../lib/contact';

// wording: BA review
const FAQS = [
  ['Video riêng tư không tải được', 'Video đặt chế độ riêng tư chỉ chủ sở hữu xem được, nên VidGrab không thể truy cập. Nếu là video của bạn, hãy chuyển sang chế độ công khai hoặc "không công khai" rồi thử lại.'],
  ['Nội dung yêu cầu đăng nhập', 'Một số bài chỉ hiện khi đã đăng nhập vào nền tảng gốc (nhóm kín, nội dung giới hạn độ tuổi, …). Với những nội dung này, dịch vụ trên web có thể không tải được. Ứng dụng Windows có thể dùng phiên đăng nhập của chính bạn ngay trên máy để xử lý một số trường hợp.'],
  ['Giới hạn theo vùng', 'Nội dung có thể bị chặn ở một số quốc gia. Nếu nền tảng gốc không cho xem ở khu vực của bạn hoặc của máy chủ, việc tải có thể thất bại.'],
  ['Link hết hạn hoặc nội dung đã bị xoá', 'Hãy mở lại link trên nền tảng gốc để chắc chắn nội dung còn xem được. Link tải do VidGrab tạo cũng tự hết hạn sau một thời gian; chỉ cần dán lại link gốc để tạo link mới.'],
  ['Nền tảng đang gặp sự cố', 'Nền tảng thay đổi thường xuyên nên đôi khi tải lỗi tạm thời. Bạn xem tình trạng từng nền tảng ngay trên trang chủ (nếu có sự cố sẽ hiện thông báo), rồi thử lại sau ít phút.'],
  ['Vì sao khách chỉ tải được tối đa 1080p?', 'Khách (chưa đăng nhập) được tải chất lượng tối đa 1080p. Đăng ký tài khoản để chọn 2K/4K khi nguồn có sẵn.'],
  ['Báo "hết lượt tải trong ngày"', 'Mỗi ngày có một hạn mức tải, tính lại vào ngày hôm sau. Đăng nhập tài khoản để có hạn mức riêng theo tài khoản.'],
  ['Tải chậm khi máy chủ bận', 'Lúc đông người dùng, yêu cầu của bạn có thể phải xếp hàng. Bạn không cần gửi lại nhiều lần — gửi lặp lại chỉ làm hàng đợi dài thêm. Hãy đợi hoặc thử lại sau.'],
  ['Tiện ích Chrome không tự cập nhật', 'Tiện ích cài thủ công (tải về và nạp ở chế độ nhà phát triển) không được Chrome tự cập nhật. Khi có bản mới, hãy tải lại tiện ích từ trang Tiện ích của VidGrab và nạp lại.'],
  ['Ứng dụng Windows bị SmartScreen cảnh báo', 'Đây là cảnh báo thường gặp với ứng dụng chưa được ký số. Xem hướng dẫn cài đặt an toàn ở phần bên dưới.'],
];

const CODE = 'rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-fg break-all';

export default function FaqPage({ onNavigate }) {
  const go = (e, view, path) => { e.preventDefault(); onNavigate?.(view, path); };
  return (
    <div className="text-fg">
      <div className="mb-8 border-b border-line pb-6">
        <h1 className="text-3xl font-extrabold text-fg mb-2">Câu hỏi thường gặp</h1>
        <p className="text-fg-muted text-sm">10 tình huống hay gặp nhất khi tải không được.</p>
      </div>

      <div className="space-y-3 mb-12">
        {FAQS.map(([q, a], i) => (
          <details key={q} className="group rounded-xl border border-line bg-surface p-4">
            <summary className="cursor-pointer list-none flex gap-3 font-semibold text-fg">
              <span className="text-fg-muted">{i + 1}.</span><span>{q}</span>
            </summary>
            <p className="mt-3 pl-7 text-sm text-fg-2 leading-relaxed">{a}</p>
          </details>
        ))}
      </div>

      <section id="cai-ung-dung-windows" className="space-y-4 text-fg-2 leading-relaxed">
        <h2 className="text-2xl font-bold text-fg">Cài ứng dụng Windows an toàn</h2>

        <h3 className="text-lg font-semibold text-fg">Vì sao Windows hiện cảnh báo?</h3>
        <p>
          Bản cài đặt hiện chưa được ký số, nên Windows SmartScreen hiện "Windows protected your PC"
          với mọi ứng dụng chưa có chữ ký. Đây là cảnh báo về việc chưa xác minh nhà phát hành, không
          có nghĩa tệp chứa mã độc. Dù vậy, bạn nên kiểm tra tệp như hướng dẫn dưới đây.
        </p>

        <h3 className="text-lg font-semibold text-fg">Cách chạy tiếp</h3>
        <ol className="list-decimal list-inside space-y-1 pl-2">
          <li>Trên hộp thoại cảnh báo, bấm <span className="text-fg font-medium">More info</span> (Thông tin thêm).</li>
          <li>Bấm <span className="text-fg font-medium">Run anyway</span> (Vẫn chạy).</li>
        </ol>

        <h3 className="text-lg font-semibold text-fg">Kiểm tra tệp cài đặt bằng SHA-256</h3>
        <p>
          Mở PowerShell tại thư mục chứa tệp vừa tải và chạy (đổi tên tệp cho đúng phiên bản):
        </p>
        <pre className="overflow-x-auto rounded-lg bg-surface-2 p-3 text-xs text-fg"><code>Get-FileHash .\VidGrab_x.y.z_x64-setup.exe -Algorithm SHA256</code></pre>
        <p>
          So sánh giá trị <span className={CODE}>Hash</span> với giá trị SHA-256 được công bố trên trang{' '}
          <a href="/download" onClick={(e) => go(e, 'download', '/download')} className="text-accent-text underline">Tải ứng dụng</a>{' '}
          hoặc bản phát hành. Nếu hai giá trị khác nhau, đừng chạy tệp — hãy tải lại từ trang chính thức
          hoặc báo cho chúng tôi.
        </p>

        <h3 className="text-lg font-semibold text-fg">Ứng dụng gửi gì lên máy chủ, giữ gì trên máy bạn?</h3>
        <ul className="list-disc list-inside space-y-2 pl-2">
          <li><span className="text-fg font-medium">Giữ trên máy bạn:</span> cookie đăng nhập các nền tảng. Cookie không rời khỏi máy và được Windows mã hoá.</li>
          <li><span className="text-fg font-medium">Gửi lên máy chủ:</span> thông tin cần để vận hành dịch vụ như tài khoản VidGrab (nếu đăng nhập), phiên bản ứng dụng, mã nhận diện máy ở dạng băm, và số lượt tải để tính hạn mức.</li>
        </ul>
        <p className="text-sm text-fg-muted">
          Chi tiết xem{' '}
          <a href="/privacy" onClick={(e) => go(e, 'privacy', '/privacy')} className="text-accent-text underline">Chính sách riêng tư</a>.
          Vẫn chưa giải quyết được? Gửi email về{' '}
          <a href={`mailto:${SUPPORT_EMAIL}`} className="text-accent-text underline break-all">{SUPPORT_EMAIL}</a>.
        </p>
      </section>
    </div>
  );
}
