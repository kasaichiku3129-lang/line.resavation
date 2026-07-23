import html
from io import BytesIO
from pathlib import Path
from textwrap import dedent

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.font_manager import FontProperties
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


TICKET_EXCLUDE_PRODUCT_NAMES = [
    "GW限定クーポン",
    "GW限定割引クーポン",
    "税抜き金額",
    "税抜金額",
]
TICKET_AMOUNT_PRODUCT_NAMES = ["税込金額"]
STEAK_CUT_PRODUCT_NAME = "黒毛和牛ステーキ切り落とし"
STEAK_CUT_PRODUCT_KEYWORDS = ["黒毛和牛", "ステーキ", "切り落とし"]


def load_csv(uploaded_file):
    try:
        return pd.read_csv(uploaded_file)
    except UnicodeDecodeError:
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, encoding="cp932")


def fill_empty_values(df):
    fill_columns = [
        "店舗名",
        "注文日時",
        "お渡し日",
        "時刻",
        "注文番号",
        "氏名",
        "お名前(かな)",
        "電話番号",
        "会計",
        "受取方法名称",
    ]
    existing_fill_columns = [column for column in fill_columns if column in df.columns]
    df[existing_fill_columns] = (
        df[existing_fill_columns].replace(["None", "none", ""], pd.NA).ffill()
    )
    missing_fill_columns = [column for column in fill_columns if column not in df.columns]
    return df, missing_fill_columns


def format_quantity(value):
    if pd.isna(value):
        return ""
    numeric_value = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric_value):
        return str(value)
    if float(numeric_value).is_integer():
        return f"{int(numeric_value):,}"
    return f"{numeric_value:,.2f}"


def format_display_value(value):
    if pd.isna(value):
        return ""
    return str(value)


def format_amount(value):
    numeric_value = pd.to_numeric(value, errors="coerce")
    if pd.notna(numeric_value):
        return f"{int(numeric_value):,}円"
    return format_display_value(value)


def format_integer_value(value):
    numeric_value = pd.to_numeric(value, errors="coerce")
    if pd.notna(numeric_value):
        return str(int(numeric_value))
    return format_display_value(value)


def matches_steak_cut_product(value):
    product_name = format_display_value(value).replace(" ", "").replace("　", "")
    return all(keyword in product_name for keyword in STEAK_CUT_PRODUCT_KEYWORDS)


def format_time_label(value):
    if pd.isna(value):
        return ""
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%H:%M")
        except (ValueError, TypeError):
            pass
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.notna(parsed):
        return parsed.strftime("%H:%M")
    return text


def time_sort_key(value):
    label = format_time_label(value)
    parsed = pd.to_datetime(label, errors="coerce")
    if pd.notna(parsed):
        return (0, parsed.hour, parsed.minute, label)
    return (1, 99, 99, label)


def sorted_time_labels(values):
    labels = {
        format_time_label(value)
        for value in values
        if format_time_label(value)
    }
    return sorted(labels, key=time_sort_key)


def get_japanese_font():
    font_paths = [
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("C:/Windows/Fonts/YuGothR.ttc"),
        Path("C:/Windows/Fonts/meiryo.ttc"),
        Path("C:/Windows/Fonts/msgothic.ttc"),
        Path("C:/Windows/Fonts/BIZ-UDGothicR.ttc"),
    ]
    for font_path in font_paths:
        if font_path.exists():
            return FontProperties(fname=str(font_path))
    return FontProperties()


def draw_ticket(ax, ticket_df, selected_date, font, bold_font):
    first_row = ticket_df.iloc[0]
    selected_name = format_display_value(first_row["お名前(かな)"])
    if selected_name and not selected_name.endswith("様"):
        selected_name = f"{selected_name}様"
    order_numbers = " / ".join(
        ticket_df["注文番号"]
        .dropna()
        .map(format_integer_value)
        .drop_duplicates()
        .tolist()
    )
    ticket_date = selected_date.strftime("%Y年%m月%d日")
    ticket_time = format_display_value(first_row["時刻"])

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    border_color = "#111111"
    line_color = "#6b7280"
    light_line_color = "#d1d5db"

    ax.add_patch(
        plt.Rectangle(
            (0.02, 0.03),
            0.96,
            0.94,
            fill=False,
            edgecolor=border_color,
            linewidth=1.2,
        )
    )
    ax.add_patch(plt.Rectangle((0.02, 0.87), 0.96, 0.1, color=border_color))
    ax.add_patch(
        plt.Rectangle(
            (0.02, 0.18),
            0.96,
            0.08,
            fill=False,
            edgecolor=line_color,
            linewidth=0.9,
        )
    )
    ax.plot([0.02, 0.98], [0.67, 0.67], color=line_color, linewidth=0.9)
    ax.plot([0.02, 0.98], [0.26, 0.26], color=line_color, linewidth=0.9)
    ax.plot([0.74, 0.74], [0.26, 0.67], color=light_line_color, linewidth=0.8)

    ax.text(
        0.05,
        0.92,
        "LINE予約注文票",
        color="white",
        fontsize=13,
        fontproperties=bold_font,
        va="center",
    )
    ax.text(
        0.95,
        0.92,
        f"注文番号: {order_numbers}",
        color="white",
        ha="right",
        va="center",
        fontsize=12.5,
        fontproperties=bold_font,
        fontweight="bold",
    )
    ax.text(0.05, 0.8, selected_name, fontsize=13, fontproperties=bold_font)
    ax.text(0.62, 0.81, ticket_date, fontsize=10.5, fontproperties=bold_font)
    ax.text(0.62, 0.74, ticket_time, fontsize=11.5, fontproperties=bold_font)

    item_rows = ticket_df[
        ticket_df["商品名"].notna()
        & ~ticket_df["商品名"].isin(
            TICKET_EXCLUDE_PRODUCT_NAMES + TICKET_AMOUNT_PRODUCT_NAMES
        )
    ][["商品名", "数量"]].head(6)
    start_y = 0.61
    row_height = 0.065
    for row_index, (_, item) in enumerate(item_rows.iterrows()):
        y = start_y - row_index * row_height
        product_name = format_display_value(item["商品名"])
        quantity = format_quantity(item["数量"])
        ax.plot([0.05, 0.95], [y - 0.026, y - 0.026], color=light_line_color, linewidth=0.6)
        ax.text(0.05, y, product_name, fontsize=9.5, fontproperties=font, va="center")
        ax.text(
            0.92,
            y,
            quantity,
            ha="right",
            fontsize=10,
            fontproperties=bold_font,
            va="center",
        )

    amount_rows = ticket_df[ticket_df["商品名"].isin(TICKET_AMOUNT_PRODUCT_NAMES)]
    total_amount = pd.to_numeric(amount_rows["金額"], errors="coerce").sum()
    ax.text(
        0.43,
        0.21,
        "税込金額:￥",
        fontsize=10,
        fontproperties=bold_font,
        va="center",
    )
    ax.text(
        0.92,
        0.21,
        f"{int(total_amount):,}",
        ha="right",
        fontsize=12,
        fontproperties=bold_font,
        va="center",
    )


def build_order_ticket_pdf(ticket_df, selected_date):
    pdf_buffer = BytesIO()
    font = get_japanese_font()
    bold_font = get_japanese_font()
    plt.rcParams["pdf.fonttype"] = 42

    tickets = []
    items_per_ticket = 6
    for _, group_df in ticket_df.groupby("お名前(かな)", sort=True):
        product_rows = group_df[
            group_df["商品名"].notna()
            & ~group_df["商品名"].isin(
                TICKET_EXCLUDE_PRODUCT_NAMES + TICKET_AMOUNT_PRODUCT_NAMES
            )
        ]
        amount_rows = group_df[group_df["商品名"].isin(TICKET_AMOUNT_PRODUCT_NAMES)]
        for start in range(0, len(product_rows), items_per_ticket):
            ticket_rows = pd.concat(
                [product_rows.iloc[start : start + items_per_ticket], amount_rows],
                ignore_index=True,
            )
            tickets.append(ticket_rows)

    with PdfPages(pdf_buffer) as pdf:
        for start in range(0, len(tickets), 4):
            page_tickets = tickets[start : start + 4]
            fig = plt.figure(figsize=(8.27, 11.69))
            axes = fig.subplots(2, 2)
            fig.subplots_adjust(
                left=0.04, right=0.96, top=0.96, bottom=0.04, wspace=0.08, hspace=0.08
            )

            cut_ax = fig.add_axes([0, 0, 1, 1], zorder=0)
            cut_ax.set_xlim(0, 1)
            cut_ax.set_ylim(0, 1)
            cut_ax.axis("off")
            cut_ax.plot(
                [0.5, 0.5],
                [0.02, 0.98],
                color="#9ca3af",
                linewidth=0.7,
                linestyle=(0, (4, 4)),
            )
            cut_ax.plot(
                [0.02, 0.98],
                [0.5, 0.5],
                color="#9ca3af",
                linewidth=0.7,
                linestyle=(0, (4, 4)),
            )

            for ax, ticket in zip(axes.flatten(), page_tickets):
                ax.set_zorder(1)
                draw_ticket(ax, ticket, selected_date, font, bold_font)

            for ax in axes.flatten()[len(page_tickets) :]:
                ax.axis("off")

            pdf.savefig(fig)
            plt.close(fig)

    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()


def build_print_report(
    selected_date,
    product_summary,
    steak_name_summary,
    selected_times=None,
    time_product_summary=None,
):
    rows = []
    for _, row in product_summary.iterrows():
        product_name = html.escape(str(row["商品名"]))
        quantity = html.escape(format_quantity(row["数量"]))
        rows.append(f"<tr><td>{product_name}</td><td>{quantity}</td></tr>")

    steak_rows = []
    for _, row in steak_name_summary.iterrows():
        order_number = html.escape(format_integer_value(row["注文番号"]))
        customer_name = html.escape(str(row["お名前(かな)"]))
        quantity = html.escape(format_quantity(row["数量"]))
        steak_rows.append(
            f"<tr><td>{order_number}</td><td>{customer_name}</td><td>{quantity}</td></tr>"
        )

    total_quantity = product_summary["数量"].sum()
    steak_total_quantity = steak_name_summary["数量"].sum()
    report_date = selected_date.strftime("%Y年%m月%d日")
    if selected_times:
        time_label = " / ".join(selected_times)
        report_meta = (
            f"お渡し日: {html.escape(report_date)}"
            f"<br>時間帯: {html.escape(time_label)}"
        )
    else:
        report_meta = f"お渡し日: {html.escape(report_date)}"

    time_summary_table = ""
    if time_product_summary is not None and not time_product_summary.empty:
        time_rows = []
        for _, row in time_product_summary.iterrows():
            time_value = html.escape(str(row["時刻"]))
            product_name = html.escape(str(row["商品名"]))
            quantity = html.escape(format_quantity(row["数量"]))
            time_rows.append(
                f"<tr><td>{time_value}</td><td>{product_name}</td><td>{quantity}</td></tr>"
            )
        time_total_quantity = time_product_summary["数量"].sum()
        time_summary_table = dedent(
            f"""
            <section class="print-section">
                <h2>時間帯別 商品数量</h2>
                <table class="print-table time-summary-table">
                    <thead>
                        <tr>
                            <th>時刻</th>
                            <th>商品名</th>
                            <th>数量</th>
                        </tr>
                    </thead>
                    <tbody>
                        {"".join(time_rows)}
                    </tbody>
                    <tfoot>
                        <tr>
                            <th colspan="2">合計</th>
                            <th>{html.escape(format_quantity(time_total_quantity))}</th>
                        </tr>
                    </tfoot>
                </table>
            </section>
            """
        ).strip()

    steak_summary_table = ""
    if steak_rows:
        steak_summary_table = dedent(
            f"""
            <section class="print-section">
                <h2>{html.escape(STEAK_CUT_PRODUCT_NAME)} お名前(かな)別数量</h2>
                <table class="print-table steak-summary-table">
                    <thead>
                        <tr>
                            <th>注文番号</th>
                            <th>お名前(かな)</th>
                            <th>数量</th>
                        </tr>
                    </thead>
                    <tbody>
                        {"".join(steak_rows)}
                    </tbody>
                    <tfoot>
                        <tr>
                            <th colspan="2">合計</th>
                            <th>{html.escape(format_quantity(steak_total_quantity))}</th>
                        </tr>
                    </tfoot>
                </table>
            </section>
            """
        ).strip()

    return dedent(
        f"""
        <div class="print-area">
            <div class="print-header">
                <h1>商品別 数量集計</h1>
                <div class="print-date">{report_meta}</div>
            </div>
            <table class="print-table">
                <thead>
                    <tr>
                        <th>商品名</th>
                        <th>数量</th>
                    </tr>
                </thead>
                <tbody>
                    {"".join(rows)}
                </tbody>
                <tfoot>
                    <tr>
                        <th>合計</th>
                        <th>{html.escape(format_quantity(total_quantity))}</th>
                    </tr>
                </tfoot>
            </table>
            {time_summary_table}
            {steak_summary_table}
        </div>
        """
    ).strip()


def apply_print_styles():
    st.markdown(
        """
        <style>
        .print-area {
            box-sizing: border-box;
            margin: 24px auto 0;
            padding: 28px 32px;
            border: 1px solid #d0d7de;
            background: #ffffff;
            color: #111827;
            max-width: 840px;
        }

        .print-header {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 24px;
            border-bottom: 2px solid #111827;
            padding-bottom: 12px;
            margin-bottom: 18px;
        }

        .print-header h1 {
            font-size: 28px;
            line-height: 1.2;
            margin: 0;
        }

        .print-date {
            font-size: 16px;
            white-space: nowrap;
        }

        .print-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 15px;
        }

        .print-table th,
        .print-table td {
            border: 1px solid #9ca3af;
            padding: 9px 12px;
        }

        .print-table th {
            background: #f3f4f6;
            text-align: left;
        }

        .print-table td:last-child,
        .print-table th:last-child {
            text-align: right;
            width: 140px;
        }

        .print-table tfoot th {
            border-top: 2px solid #111827;
            font-size: 16px;
        }

        .print-section {
            margin-top: 28px;
        }

        .print-section h2 {
            font-size: 20px;
            line-height: 1.35;
            margin: 0 0 10px;
        }

        .steak-summary-table th:first-child,
        .steak-summary-table td:first-child {
            width: 120px;
        }

        .time-summary-table th:first-child,
        .time-summary-table td:first-child {
            width: 100px;
        }

        .time-summary-table td:last-child,
        .time-summary-table th:last-child {
            width: 140px;
        }

        @media print {
            @page {
                size: A4;
                margin: 12mm;
            }

            html,
            body,
            [data-testid="stAppViewContainer"],
            [data-testid="stMain"],
            .main,
            .block-container {
                margin: 0 !important;
                padding: 0 !important;
                width: 100% !important;
                max-width: none !important;
                min-height: 0 !important;
                height: auto !important;
                overflow: visible !important;
            }

            body * {
                visibility: hidden !important;
            }

            .print-area,
            .print-area * {
                visibility: visible !important;
            }

            .print-area {
                position: fixed !important;
                left: 50% !important;
                top: 0 !important;
                transform: translateX(-50%);
                box-sizing: border-box;
                width: 186mm;
                max-width: 186mm;
                margin: 0 !important;
                padding: 0 !important;
                border: 0 !important;
                page-break-inside: avoid;
                break-inside: avoid;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


st.set_page_config(page_title="注文集計アプリ", layout="wide")
apply_print_styles()

st.sidebar.title("メニュー")
menu = st.sidebar.radio("表示する画面", ["アップロード", "集計", "注文票PDF"])

if "df" in st.session_state:
    st.sidebar.success("CSV読み込み済み")
else:
    st.sidebar.info("CSV未読み込み")

if menu == "アップロード":
    st.title("CSV読み込み")

    uploaded_file = st.file_uploader("CSVファイルを選択してください", type=["csv"])
    if uploaded_file is None:
        st.info("CSVファイルをアップロードすると内容を表示します。")
        st.stop()

    try:
        df = load_csv(uploaded_file)
        df, missing_fill_columns = fill_empty_values(df)
        st.session_state.df = df
        st.session_state.missing_fill_columns = missing_fill_columns

        if missing_fill_columns:
            st.warning(
                f"次の列が見つからなかったため補完をスキップしました: {', '.join(missing_fill_columns)}"
            )

        st.success("CSVを読み込みました。")
        st.write(f"{df.shape[0]} 行 x {df.shape[1]} 列")
        st.dataframe(df, use_container_width=True)
    except Exception as error:
        st.error(f"CSVの読み込みに失敗しました: {error}")
        st.stop()

elif menu == "集計":
    st.title("商品別の数量集計")
    if "df" not in st.session_state:
        st.info("先にアップロード画面でCSVファイルを読み込んでください。")
        st.stop()

    df = st.session_state.df
    required_summary_columns = ["お渡し日", "商品名", "数量", "お名前(かな)", "注文番号"]
    missing_summary_columns = [
        column for column in required_summary_columns if column not in df.columns
    ]
    if missing_summary_columns:
        st.error(f"集計に必要な列が見つかりません: {', '.join(missing_summary_columns)}")
        st.stop()

    has_time_column = "時刻" in df.columns
    summary_df = df.copy()
    summary_df["集計日"] = pd.to_datetime(summary_df["お渡し日"], errors="coerce").dt.date
    summary_df["集計数量"] = pd.to_numeric(summary_df["数量"], errors="coerce").fillna(0)
    if has_time_column:
        summary_df["集計時刻"] = summary_df["時刻"].map(format_time_label)
    exclude_product_names = ["GW限定割引クーポン", "税抜金額", "税込金額"]
    summary_df = summary_df[~summary_df["商品名"].isin(exclude_product_names)]
    available_dates = sorted(summary_df["集計日"].dropna().unique())

    if not available_dates:
        st.warning("お渡し日として読み取れる日付がありません。")
        st.stop()

    selected_date = st.date_input(
        "お渡し日",
        value=available_dates[0],
        min_value=available_dates[0],
        max_value=available_dates[-1],
    )
    selected_summary_df = summary_df[summary_df["集計日"] == selected_date].copy()
    selected_times = None
    time_product_summary = pd.DataFrame(columns=["時刻", "商品名", "数量"])

    if has_time_column:
        available_times = sorted_time_labels(selected_summary_df["集計時刻"])
        if available_times:
            selected_times = st.multiselect(
                "時間帯",
                available_times,
                default=available_times,
                help="選択した時刻のお渡し分だけを集計します。",
            )
            if not selected_times:
                st.warning("集計する時間帯を選択してください。")
                st.stop()
            selected_summary_df = selected_summary_df[
                selected_summary_df["集計時刻"].isin(selected_times)
            ]
        else:
            st.info("選択したお渡し日には読み取れる時刻がありません。日全体で集計します。")
    else:
        st.info("CSVに「時刻」列がないため、時間帯別集計は利用できません。")

    product_summary = (
        selected_summary_df.dropna(subset=["商品名"])
        .groupby("商品名", as_index=False)["集計数量"]
        .sum()
        .rename(columns={"集計数量": "数量"})
        .sort_values("商品名")
    )

    if has_time_column and selected_times:
        time_product_summary = (
            selected_summary_df.dropna(subset=["商品名"])
            .groupby(["集計時刻", "商品名"], as_index=False)["集計数量"]
            .sum()
            .rename(columns={"集計時刻": "時刻", "集計数量": "数量"})
        )
        time_product_summary["_時刻順"] = time_product_summary["時刻"].map(time_sort_key)
        time_product_summary = time_product_summary.sort_values(
            ["_時刻順", "商品名"]
        ).drop(columns=["_時刻順"])

    selected_summary_df["ステーキ切り落とし対象"] = selected_summary_df["商品名"].map(
        matches_steak_cut_product
    )
    steak_name_summary = (
        selected_summary_df[selected_summary_df["ステーキ切り落とし対象"]]
        .dropna(subset=["注文番号", "お名前(かな)"])
        .groupby(["注文番号", "お名前(かな)"], as_index=False)["集計数量"]
        .sum()
        .rename(columns={"集計数量": "数量"})
        .sort_values(["注文番号", "お名前(かな)"])
    )

    st.subheader("商品別 数量")
    st.dataframe(product_summary, use_container_width=True, hide_index=True)

    if has_time_column and selected_times:
        st.subheader("時間帯別 商品数量")
        if time_product_summary.empty:
            st.info("選択した時間帯には商品データがありません。")
        else:
            st.dataframe(time_product_summary, use_container_width=True, hide_index=True)

    st.subheader(f"{STEAK_CUT_PRODUCT_NAME} お名前別数量")
    if steak_name_summary.empty:
        st.info("選択した条件には対象商品のデータがありません。")
    else:
        st.dataframe(steak_name_summary, use_container_width=True, hide_index=True)

    components.html(
        """
        <button
            onclick="window.parent.print()"
            style="
                appearance: none;
                border: 1px solid #2563eb;
                background: #2563eb;
                color: white;
                border-radius: 6px;
                padding: 9px 16px;
                font-size: 14px;
                font-weight: 600;
                cursor: pointer;
            "
        >
            この内容で印刷
        </button>
        """,
        height=48,
    )
    st.markdown(
        build_print_report(
            selected_date,
            product_summary,
            steak_name_summary,
            selected_times=selected_times,
            time_product_summary=time_product_summary,
        ),
        unsafe_allow_html=True,
    )

elif menu == "注文票PDF":
    st.title("注文票PDF")
    if "df" not in st.session_state:
        st.info("先にアップロード画面でCSVファイルを読み込んでください。")
        st.stop()

    df = st.session_state.df
    required_ticket_columns = [
        "お名前(かな)",
        "注文番号",
        "お渡し日",
        "時刻",
        "商品名",
        "数量",
        "金額",
    ]
    missing_ticket_columns = [
        column for column in required_ticket_columns if column not in df.columns
    ]
    if missing_ticket_columns:
        st.error(f"注文票PDFに必要な列が見つかりません: {', '.join(missing_ticket_columns)}")
        st.stop()

    ticket_df = df.copy()
    ticket_df["集計日"] = pd.to_datetime(ticket_df["お渡し日"], errors="coerce").dt.date
    available_dates = sorted(ticket_df["集計日"].dropna().unique())
    if not available_dates:
        st.warning("お渡し日として読み取れる日付がありません。")
        st.stop()

    selected_date = st.date_input(
        "お渡し日",
        value=available_dates[0],
        min_value=available_dates[0],
        max_value=available_dates[-1],
    )
    selected_ticket_df = ticket_df[
        (ticket_df["集計日"] == selected_date)
        & ticket_df["お名前(かな)"].notna()
        & ticket_df["商品名"].notna()
    ].copy()
    selected_ticket_df = selected_ticket_df[
        ~selected_ticket_df["商品名"].isin(TICKET_EXCLUDE_PRODUCT_NAMES)
    ]

    if selected_ticket_df.empty:
        st.warning("選択したお渡し日にPDFへ出力できる注文データがありません。")
        st.stop()

    available_names = sorted(
        selected_ticket_df["お名前(かな)"].dropna().astype(str).unique().tolist()
    )
    selected_names = st.multiselect(
        "PDF作成するお名前(かな)",
        available_names,
        default=available_names,
    )
    if not selected_names:
        st.warning("PDF作成するお名前(かな)を選択してください。")
        st.stop()

    selected_ticket_df = selected_ticket_df[
        selected_ticket_df["お名前(かな)"].astype(str).isin(selected_names)
    ]

    ticket_count = 0
    for _, group_df in selected_ticket_df.groupby("お名前(かな)", sort=True):
        product_rows = group_df[
            ~group_df["商品名"].isin(
                TICKET_EXCLUDE_PRODUCT_NAMES + TICKET_AMOUNT_PRODUCT_NAMES
            )
        ]
        ticket_count += -(-len(product_rows) // 6)

    if ticket_count == 0:
        st.warning("PDFに出力できる商品データがありません。")
        st.stop()

    person_count = selected_ticket_df["お名前(かな)"].nunique()
    page_count = -(-ticket_count // 4)
    st.write(f"{person_count}名分、{ticket_count}枚の注文票を作成します。A4 {page_count}ページです。")

    pdf_bytes = build_order_ticket_pdf(selected_ticket_df, selected_date)
    file_date = selected_date.strftime("%Y%m%d")
    file_suffix = "全員分" if len(selected_names) == len(available_names) else "選択分"
    st.download_button(
        "注文票PDFをダウンロード",
        data=pdf_bytes,
        file_name=f"注文票_{file_date}_{file_suffix}.pdf",
        mime="application/pdf",
    )

    preview_columns = ["お名前(かな)", "注文番号", "お渡し日", "時刻", "商品名", "数量", "金額"]
    st.write("PDF作成対象")
    st.dataframe(
        selected_ticket_df[preview_columns],
        use_container_width=True,
        hide_index=True,
    )
