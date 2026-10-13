import { SUPPORT_EMAIL } from '../lib/contact';

// wording: BA review
export default function TermsPage({ onNavigate }) {
  const go = (e, view, path) => { e.preventDefault(); onNavigate?.(view, path); };
  return (
    <div className="text-fg">
      <div className="mb-10 border-b border-line pb-6">
        <h1 className="text-3xl font-extrabold text-fg mb-2">Điều khoản sử dụng</h1>
        <p className="text-fg-muted text-sm">
          Cập nhật lần cuối: <span className="text-fg-2">13/10/2026</span>
        </p>
      </div>

      <div className="space-y-10 text-fg-2 leading-relaxed">
        <section>
          <h2 className="text-xl font-bold text-fg mb-3">1. Dịch vụ này dùng để làm gì</h2>
          <p>
            VidGrab giúp bạn lưu lại nội dung mà bạn có quyền lưu: video của chính bạn, nội dung được
            chủ sở hữu cho phép, hoặc nội dung được pháp luật cho phép dùng cho mục đích cá nhân.
            Khi dùng VidGrab, bạn đồng ý với các điều khoản dưới đây.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">2. Trách nhiệm của bạn</h2>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <li>Bạn tự chịu trách nhiệm về nội dung mình tải và cách bạn sử dụng nội dung đó.</li>
            <li>Bạn cần tuân thủ điều khoản của từng nền tảng gốc (YouTube, TikTok, Facebook, …) và luật bản quyền hiện hành.</li>
            <li>VidGrab không cấp cho bạn bất kỳ quyền nào đối với nội dung của người khác.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">3. Không bảo đảm nền tảng nào cũng dùng được mọi lúc</h2>
          <p>
            Các nền tảng thường xuyên thay đổi cách hoạt động. Vì vậy có thể có lúc một nền tảng
            tải chậm, lỗi hoặc tạm thời không tải được. Chúng tôi cố gắng khắc phục sớm nhưng không
            cam kết dịch vụ hoạt động liên tục hay thành công với mọi liên kết.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">4. Những việc không được làm</h2>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <li>Tải nội dung khi chưa được phép rồi đăng lại, bán lại hoặc phân phối lại.</li>
            <li>Dùng công cụ tự động, gửi yêu cầu dồn dập vượt quá giới hạn cho phép của dịch vụ.</li>
            <li>Tìm cách lách giới hạn lượt tải (ví dụ tạo nhiều tài khoản, đổi địa chỉ để lấy thêm lượt).</li>
            <li>Dùng VidGrab vào mục đích vi phạm pháp luật hoặc xâm phạm quyền của người khác.</li>
          </ul>
          <p className="mt-3">Vi phạm có thể dẫn đến việc hạn chế hoặc khoá quyền truy cập.</p>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">5. Tài khoản và giới hạn sử dụng</h2>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <li><span className="text-fg font-medium">Khách (chưa đăng nhập):</span> tải được chất lượng tối đa 1080p, có giới hạn số lượt mỗi ngày.</li>
            <li><span className="text-fg font-medium">Tài khoản đã đăng ký:</span> được chọn chất lượng 2K/4K (nếu nguồn có) và có hạn mức riêng.</li>
            <li>Hạn mức cụ thể có thể được điều chỉnh để giữ dịch vụ ổn định cho mọi người.</li>
            <li>Bạn chịu trách nhiệm giữ an toàn thông tin đăng nhập của mình.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">6. Gói trả phí</h2>
          <p>
            Các gói trả phí sẽ áp dụng chính sách giá và hoàn tiền riêng, sẽ công bố khi mở bán.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">7. Yêu cầu gỡ nội dung</h2>
          <p>
            Nếu bạn là chủ sở hữu nội dung (hoặc người được ủy quyền) và muốn yêu cầu gỡ,
            vui lòng gửi yêu cầu tại{' '}
            <a href="/takedown" onClick={(e) => go(e, 'takedown', '/takedown')} className="text-accent-text underline">/takedown</a>.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">8. Giới hạn trách nhiệm và thay đổi điều khoản</h2>
          <p>
            Dịch vụ được cung cấp theo hiện trạng. Chúng tôi có thể cập nhật điều khoản này; ngày cập
            nhật gần nhất được ghi ở đầu trang. Tiếp tục sử dụng sau khi cập nhật nghĩa là bạn đồng ý
            với bản mới. Văn bản này là mô tả chung, không phải tư vấn pháp lý.
          </p>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">9. Liên hệ</h2>
          <p>
            Mọi thắc mắc xin gửi về{' '}
            <a href={`mailto:${SUPPORT_EMAIL}`} className="text-accent-text underline break-all">{SUPPORT_EMAIL}</a>.
          </p>
        </section>
      </div>
    </div>
  );
}
