// wording: BA review
// Task #6398 (13/10/2026): rewritten to match what the code actually stores —
// see docs/security/SECURITY-REVIEW-2026-10-13.md §D for the evidence per line.
// Do not add a practice here unless the code does it.

function Section({ title, children }) {
  return (
    <section>
      <h2 className="text-xl font-bold text-fg mb-3">{title}</h2>
      {children}
    </section>
  );
}

function Item({ label, children }) {
  return (
    <li>
      <span className="text-fg font-medium">{label}</span> — {children}
    </li>
  );
}

export default function PrivacyPolicy() {
  return (
    <div className="text-fg">
      {/* Header */}
      <div className="mb-10 border-b border-line pb-6">
        <h1 className="text-3xl font-extrabold text-fg mb-2">Chính sách Bảo mật</h1>
        <p className="text-fg-muted text-sm">
          Privacy Policy — <span className="text-fg-2">Cập nhật lần cuối: 13/10/2026</span>
        </p>
      </div>

      <div className="space-y-10 text-fg-2 leading-relaxed">

        <Section title="1. Dữ liệu chúng tôi lưu">
          <p className="mb-3">
            Trang này liệt kê những gì VidGrab thực sự lưu khi bạn dùng website, tiện ích trình duyệt và ứng dụng Windows.
          </p>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <Item label="Tài khoản">
              email và thông tin đăng nhập (qua Supabase Auth), gói dịch vụ, hạn mức đã dùng.
            </Item>
            <Item label="Lịch sử tải">
              đường dẫn video, tiêu đề, nền tảng, định dạng và thời điểm tải. Khi file hết hạn, thông tin này vẫn được giữ để bạn tra cứu lại.
            </Item>
            <Item label="Tùy chọn và tính năng bạn bật">
              cài đặt mặc định, kênh bạn theo dõi, lịch tải, bộ sưu tập, ghi chú, webhook bạn khai báo, liên kết Telegram, đăng ký nhận thông báo đẩy.
            </Item>
            <Item label="Địa chỉ IP">
              dùng để tính hạn mức cho khách chưa đăng nhập và chống lạm dụng. IP được lưu ở dạng gốc trong nhật ký bảo mật và bộ đếm hạn mức (xem mục 3). Riêng yêu cầu gỡ nội dung chỉ lưu IP ở dạng mã băm.
            </Item>
            <Item label="Thông tin trình duyệt">
              loại trình duyệt (user agent) trong nhật ký bảo mật.
            </Item>
            <Item label="Thống kê sử dụng">
              sự kiện thao tác trên trang, gắn với tài khoản hoặc một mã ẩn danh của trình duyệt.
            </Item>
          </ul>
        </Section>

        <Section title="2. Những gì chúng tôi không lưu">
          <ul className="list-disc list-inside space-y-2 pl-2">
            <li>Số thẻ hoặc thông tin thẻ thanh toán. Việc thanh toán do Stripe xử lý.</li>
            <li>Video lâu dài trên máy chủ. Một số nền tảng cần máy chủ tải file về trước rồi mới chuyển cho bạn. Các file này bị xoá tự động, thường trong khoảng 2 giờ.</li>
            <li>Dữ liệu sinh trắc học, sức khỏe hay danh bạ, tin nhắn trên thiết bị của bạn.</li>
          </ul>
        </Section>

        <Section title="3. Thời gian lưu">
          <ul className="list-disc list-inside space-y-2 pl-2">
            <Item label="Link tải">hết hạn sau khoảng 60 phút kể từ khi tạo.</Item>
            <Item label="File tạm trên máy chủ">tự xoá, mặc định sau 2 giờ.</Item>
            <Item label="Lịch sử tải">
              giữ đến khi bạn xoá từng mục trong phần "Lịch sử".
            </Item>
            <Item label="Bộ đếm hạn mức và chống lạm dụng (có IP, mã máy)">
              tự xoá sau 1 đến 40 ngày, tuỳ loại bộ đếm.
            </Item>
            <Item label="Nhật ký bảo mật (có IP, trình duyệt)">
              hiện chưa có lịch xoá tự động.
            </Item>
            <Item label="Yêu cầu gỡ nội dung">
              giữ 1.000 yêu cầu gần nhất. Yêu cầu cũ hơn bị xoá khi có yêu cầu mới.
            </Item>
            <Item label="Bản sao lưu cơ sở dữ liệu">
              giữ 14 ngày. Dữ liệu bạn đã xoá có thể còn trong bản sao lưu đến hết thời hạn này.
            </Item>
            <Item label="Tài khoản">
              giữ đến khi bạn gửi email yêu cầu xoá. Hiện chưa có nút tự xoá tài khoản.
            </Item>
          </ul>
        </Section>

        <Section title="4. Tiện ích trình duyệt (Chrome)">
          <ul className="list-disc list-inside space-y-2 pl-2">
            <Item label="activeTab, tabs, scripting">
              đọc địa chỉ trang đang mở và nhận diện video trên trang.
            </Item>
            <Item label="cookies">
              khi bạn tải video cần đăng nhập, tiện ích đọc cookie của chính trang video đó và gửi kèm yêu cầu tải. Cookie chỉ được gửi đến máy chủ VidGrab. Máy chủ chỉ dùng cookie cho lần tải đó rồi xoá ngay, không lưu lại.
            </Item>
            <Item label="downloads, storage, notifications, contextMenus, alarms">
              lưu file về máy, giữ phiên đăng nhập và cài đặt trong trình duyệt, hiện thông báo, thêm menu chuột phải.
            </Item>
          </ul>
        </Section>

        <Section title="5. Ứng dụng VidGrab cho Windows">
          <ul className="list-disc list-inside space-y-2 pl-2">
            <Item label="Cookie đăng nhập nền tảng">
              nằm trên máy bạn, được mã hoá bằng cơ chế của Windows, và không gửi về máy chủ VidGrab.
            </Item>
            <Item label="Mã máy">
              ứng dụng gửi một mã băm từ mã định danh của Windows (không gửi mã gốc), kèm tên máy, phiên bản Windows và phiên bản ứng dụng. Mã này dùng để tính lượt tải miễn phí. Máy chủ lưu kèm địa chỉ IP gần nhất của máy.
            </Item>
            <Item label="Đồng bộ lịch sử">
              nếu bạn bật, ứng dụng gửi đường dẫn, tiêu đề, định dạng và kết quả mỗi lượt tải. Đường dẫn thư mục lưu file trên máy bạn không được gửi.
            </Item>
          </ul>
        </Section>

        <Section title="6. Bên thứ ba nhận dữ liệu">
          <ul className="list-disc list-inside space-y-2 pl-2">
            <Item label="Supabase">đăng nhập và cơ sở dữ liệu.</Item>
            <Item label="Stripe">thanh toán. Nhận email của bạn khi bạn mua gói.</Item>
            <Item label="Dịch vụ tải trung gian (như Apify, ScraperAPI)">
              với một số nền tảng, đường dẫn video bạn gửi được chuyển qua các dịch vụ này để lấy video.
            </Item>
            <Item label="Dịch vụ AI (Gemini, OpenAI/Whisper)">
              chỉ khi bạn dùng tính năng phiên âm, dịch hoặc phân tích video. Khi đó âm thanh hoặc nội dung chữ của video được gửi đi để xử lý.
            </Item>
            <Item label="Telegram và email">
              nội dung góp ý (tên, email, nội dung, IP) và thông báo có yêu cầu gỡ nội dung mới được gửi đến nhóm vận hành. Góp ý không lưu trong cơ sở dữ liệu.
            </Item>
            <Item label="Sentry">
              nếu được bật, nhận báo cáo lỗi kỹ thuật, không kèm thông tin cá nhân.
            </Item>
            <Item label="Google Fonts">
              trang web tải phông chữ từ Google, nên Google nhận địa chỉ IP của trình duyệt bạn.
            </Item>
            <Item label="Dịch vụ thông báo đẩy của trình duyệt">
              nhận nội dung thông báo khi bạn bật thông báo đẩy.
            </Item>
          </ul>
          <p className="mt-4 font-semibold text-danger">
            Chúng tôi KHÔNG bán, cho thuê hoặc chia sẻ dữ liệu của bạn cho bất kỳ bên thứ ba nào cho mục đích quảng cáo.
          </p>
        </Section>

        <Section title="7. Yêu cầu gỡ nội dung">
          <p>
            Khi gửi yêu cầu gỡ nội dung, chúng tôi lưu họ tên, email, tư cách người gửi, đường dẫn nội dung, mô tả và mã băm địa chỉ IP của bạn. Thông tin này chỉ dùng để xử lý yêu cầu.
          </p>
        </Section>

        <Section title="8. Liên hệ">
          <p className="mb-4">
            Nếu bạn có câu hỏi về chính sách này hoặc muốn yêu cầu xoá dữ liệu, vui lòng liên hệ:
          </p>
          <a
            href="mailto:privacy@vidgrab.io"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent-soft border border-accent/30 text-accent-text font-semibold hover:bg-accent/20 transition-colors"
          >
            privacy@vidgrab.io
          </a>
          <p className="mt-4 text-fg-muted text-sm">
            Chúng tôi cam kết phản hồi trong vòng 5 ngày làm việc.
          </p>
        </Section>

      </div>
    </div>
  );
}
