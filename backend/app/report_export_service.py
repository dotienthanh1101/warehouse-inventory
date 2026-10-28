"""Readonly exports of immutable day reports, scoped by the authenticated warehouse."""
import csv
import io
from datetime import date, datetime

from backend.app.database import get_connection
from backend.app.day_closing_service import _build_report
from backend.app.daily_inventory_service import VIETNAM_TIMEZONE

HEADERS = ('ID marker', 'Mã sản phẩm', 'Tên loại hàng', 'Tồn đầu',
           'Tăng qua kiểm kê', 'Giảm qua kiểm kê', 'Tồn cuối', 'Đơn vị')
QUANTITIES = ('opening_quantity', 'total_increase', 'total_decrease', 'closing_quantity')
TOTALS = ('opening_total_boxes', 'total_increase', 'total_decrease', 'closing_total_boxes')
NOTE = 'Tăng/giảm qua kiểm kê, không phải tổng nhập/xuất giao dịch thực tế.'


def load_closed_report(day_id):
    with get_connection() as connection:
        connection.execute('BEGIN')
        day = connection.execute('SELECT status FROM inventory_days WHERE day_id=?', (day_id,)).fetchone()
        if day is None:
            raise LookupError('Không tìm thấy ngày kiểm kê trong kho của bạn.')
        if day['status'] != 'closed':
            raise ValueError('Chốt ngày trước khi xuất báo cáo chính thức.')
        report = _build_report(connection, day_id)
    validate_report(report)
    return report


def validate_report(report):
    if date.fromisoformat(report['business_date']).isoformat() != report['business_date']:
        raise ValueError('Ngày kiểm kê không hợp lệ.')
    if type(report['session_count']) is not int or report['session_count'] < 1:
        raise ValueError('Báo cáo đã chốt thiếu phiên kiểm kê.')
    if report['status'] != 'closed' or not report['closed_at'] or not report['items']:
        raise ValueError('Báo cáo đã chốt thiếu dữ liệu; chưa thể xuất.')
    if len({row['marker_id'] for row in report['items']}) != len(report['items']):
        raise ValueError('Báo cáo có mã loại hàng trùng; cần kiểm tra dữ liệu đã chốt.')
    for row in report['items']:
        if any(type(row[k]) is not int or row[k] < 0 for k in QUANTITIES):
            raise ValueError('Số lượng trong báo cáo chưa hợp lệ.')
        if row['opening_quantity'] + row['total_increase'] - row['total_decrease'] != row['closing_quantity']:
            raise ValueError('Tồn đầu + tăng − giảm không khớp tồn cuối; chưa xuất báo cáo.')
    for key, total in zip(QUANTITIES, TOTALS):
        if sum(row[key] for row in report['items']) != report[total]:
            raise ValueError('Tổng báo cáo không khớp dữ liệu chi tiết.')


def local_time(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Thời điểm chốt thiếu múi giờ; cần kiểm tra dữ liệu.')
    return stamp.astimezone(VIETNAM_TIMEZONE).strftime('%d/%m/%Y %H:%M:%S')


def metadata(report, warehouse):
    return [('Kho', warehouse['name']), ('Mã kho', warehouse['warehouse_id']),
            ('Ngày kiểm kê', report['business_date']), ('Trạng thái', 'Đã chốt'),
            ('Chốt lúc (giờ Việt Nam)', local_time(report['closed_at'])),
            ('Số phiên đã chốt', report['session_count']), ('Đơn vị tính', 'kiện'),
            ('Mã ngày kiểm kê', report['day_id']), ('Ghi chú', NOTE)]


def data_rows(report):
    for row in report['items']:
        yield (f"{row['marker_id']:02d}", row['product_code'], row['product_name'],
               *(row[k] for k in QUANTITIES), 'kiện')


def safe_csv_text(value):
    if isinstance(value, str) and (value[:1] in ('\t', '\r', '\n')
                                  or value.lstrip().startswith(('=', '+', '-', '@'))):
        return "'" + value
    return value


def render_csv(report, warehouse):
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\r\n', quoting=csv.QUOTE_ALL)
    rows = [('BÁO CÁO KIỂM KÊ NGÀY',), *metadata(report, warehouse), (), HEADERS,
            *data_rows(report), ('', '', 'TỔNG', *(report[k] for k in TOTALS), 'kiện')]
    for row in rows:
        writer.writerow([safe_csv_text(value) for value in row])
    return output.getvalue().encode('utf-8-sig')


def render_xlsx(report, warehouse):
    # A dependency of the user-installed Python application, not the Codex runtime.
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    book = Workbook()
    sheet = book.active
    sheet.title = 'Báo cáo ngày'
    sheet.sheet_view.showGridLines = False
    book.properties.creator = 'Hệ thống kiểm kê kho'
    book.properties.title = 'Báo cáo kiểm kê ngày ' + report['business_date']
    blue = '244D89'
    pale = 'EAF2FF'

    def text(row, col, value):
        cell = sheet.cell(row, col, value)
        if isinstance(value, str):
            # Treat every database/user string as text, including leading '='.
            cell.data_type = 's'
        return cell

    sheet.merge_cells('A1:H2')
    for row in sheet['A1:H2']:
        for cell in row:
            cell.fill = PatternFill('solid', fgColor=blue)
    title = text(1, 1, 'BÁO CÁO KIỂM KÊ NGÀY')
    title.font = Font(name='Calibri', size=20, bold=True, color='FFFFFF')
    title.fill = PatternFill('solid', fgColor=blue)
    title.alignment = Alignment(vertical='center', horizontal='left')
    sheet.row_dimensions[1].height = 24
    sheet.row_dimensions[2].height = 14
    for row, (label, value) in enumerate(metadata(report, warehouse), 4):
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        sheet.merge_cells(start_row=row, start_column=3, end_row=row, end_column=8)
        text(row, 1, label).font = Font(name='Calibri', size=11, bold=True, color=blue)
        text(row, 3, value).font = Font(name='Calibri', size=11, color='17263C')
        sheet.cell(row, 3).alignment = Alignment(wrap_text=True, vertical='center', horizontal='left')
        sheet.row_dimensions[row].height = 32 if label == 'Ghi chú' else 18

    header = 15
    for col, name in enumerate(HEADERS, 1):
        cell = text(header, col, name)
        cell.font = Font(name='Calibri', size=11, bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor=blue)
        cell.alignment = Alignment(wrap_text=True, vertical='center', horizontal='center')
    sheet.row_dimensions[header].height = 34
    for row_number, values in enumerate(data_rows(report), header + 1):
        for col, value in enumerate(values, 1):
            cell = text(row_number, col, value)
            cell.font = Font(name='Calibri', size=11, color='17263C')
            cell.alignment = Alignment(vertical='center', wrap_text=True, indent=1 if 4 <= col <= 7 else 0,
                                       horizontal='right' if 4 <= col <= 7 else 'center' if col in (1, 8) else 'left')
            if row_number % 2 == 0:
                cell.fill = PatternFill('solid', fgColor='F2F6FC')
            if 4 <= col <= 7:
                cell.number_format = '#,##0'
        # Keep long historical names visible in the printed report.
        name_length = len(str(values[2]))
        sheet.row_dimensions[row_number].height = max(20, min(150, 14 * (name_length // 32 + 1)))
    end = header + len(report['items'])
    total_row = end + 1
    for col, value in enumerate(('', '', 'TỔNG', *(report[k] for k in TOTALS), 'kiện'), 1):
        cell = text(total_row, col, value)
        cell.font = Font(name='Calibri', size=12, bold=True, color=blue)
        cell.fill = PatternFill('solid', fgColor=pale)
        cell.number_format = '#,##0' if 4 <= col <= 7 else 'General'
        cell.alignment = Alignment(horizontal='right' if 4 <= col <= 7 else 'center' if col == 8 else 'left',
                                   indent=1 if 4 <= col <= 7 else 0, vertical='center')
        cell.border = Border(top=Side(style='thin', color=blue))
    sheet.row_dimensions[total_row].height = 28
    for col, width in enumerate((12, 19, 40, 14, 18, 18, 14, 12), 1):
        sheet.column_dimensions[get_column_letter(col)].width = width
    sheet.freeze_panes = 'D16'
    sheet.auto_filter.ref = f'A15:H{end}'
    sheet.print_title_rows = '15:15'
    sheet.print_options.horizontalCentered = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_area = f'A1:H{total_row}'
    sheet.oddFooter.center.text = 'Trang &P / &N'
    sheet.oddFooter.right.text = 'Báo cáo đã chốt'
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()
