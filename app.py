import html
from datetime import date
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
SALES_EXCLUDE_PRODUCT_NAMES = TICKET_EXCLUDE_PRODUCT_NAMES + TICKET_AMOUNT_PRODUCT_NAMES
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


def sanitize_print_text(value):
    text = format_display_value(value)
    return " ".join(text.replace("\u3000", " ").split())


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


def prepare_sales_df(df):
    sales_df = df.copy()
    sales_df["集計日"] = pd.to_datetime(sales_df["お渡し日"], errors="coerce").dt.date
    sales_df["集計月"] = pd.to_datetime(sales_df["お渡し日"], errors="coerce").dt.to_period("M")
    if "店舗名" in sales_df.columns:
        sales_df["店舗名"] = (
            sales_df["店舗名"]
            .map(sanitize_print_text)
            .replace("", pd.NA)
            .fillna("店舗名なし")
        )
    if "商品名" in sales_df.columns:
        sales_df["商品名"] = sales_df["商品名"].map(
            lambda value: sanitize_print_text(value) if pd.notna(value) else value
        )

    quantity = pd.to_numeric(sales_df["数量"], errors="coerce")
    unit_price = pd.to_numeric(sales_df["金額"], errors="coerce").fillna(0)
    # CSVは数量が複数のとき金額列が単価のみになるため、数量>1は数量×単価で計上する。
    # 税込・税抜・クーポン行は金額をそのまま使う。
    is_product_row = (
        sales_df["商品名"].notna()
        & (sales_df["商品名"] != "")
        & ~sales_df["商品名"].isin(SALES_EXCLUDE_PRODUCT_NAMES)
    )
    multiply_mask = is_product_row & quantity.notna() & (quantity > 1)
    sales_df["売上金額"] = unit_price
    sales_df.loc[multiply_mask, "売上金額"] = (
        quantity[multiply_mask] * unit_price[multiply_mask]
    )
    sales_df["集計数量"] = quantity.fillna(0)
    sales_df["単価"] = unit_price
    return sales_df


def filter_order_sales_df(sales_df):
    """注文票と同じく、商品名が「税込金額」の行を売上として使う。"""
    return sales_df[sales_df["商品名"].isin(TICKET_AMOUNT_PRODUCT_NAMES)].copy()


def is_nonzero_sales_amount(series):
    amount = pd.to_numeric(series, errors="coerce").fillna(0)
    return amount != 0


def filter_product_sales_df(sales_df):
    """商品別集計用。クーポン・税抜・税込の行と0円の商品は除外する。"""
    product_df = sales_df[
        sales_df["商品名"].notna()
        & (sales_df["商品名"] != "")
        & ~sales_df["商品名"].isin(SALES_EXCLUDE_PRODUCT_NAMES)
    ].copy()
    return product_df[is_nonzero_sales_amount(product_df["売上金額"])].copy()


def build_sales_amount_reconciliation(product_df, order_df):
    product_total = float(product_df["売上金額"].sum()) if not product_df.empty else 0.0
    order_total = float(order_df["売上金額"].sum()) if not order_df.empty else 0.0
    difference = product_total - order_total
    matched = abs(difference) < 0.5

    store_names = sorted(
        set(product_df["店舗名"].dropna().tolist() if not product_df.empty else [])
        | set(order_df["店舗名"].dropna().tolist() if not order_df.empty else [])
    )
    store_rows = []
    for store_name in store_names:
        product_amount = float(
            product_df.loc[product_df["店舗名"] == store_name, "売上金額"].sum()
        )
        order_amount = float(
            order_df.loc[order_df["店舗名"] == store_name, "売上金額"].sum()
        )
        store_difference = product_amount - order_amount
        store_rows.append(
            {
                "店舗名": store_name,
                "商品合計": product_amount,
                "税込金額": order_amount,
                "差額": store_difference,
                "一致": abs(store_difference) < 0.5,
            }
        )
    store_compare = pd.DataFrame(store_rows)

    mismatch_orders = pd.DataFrame(
        columns=["注文番号", "店舗名", "商品合計", "税込金額", "差額"]
    )
    if (
        "注文番号" in product_df.columns
        and "注文番号" in order_df.columns
        and not product_df.empty
        and not order_df.empty
    ):
        product_by_order = (
            product_df.dropna(subset=["注文番号"])
            .groupby(["注文番号", "店舗名"], as_index=False)["売上金額"]
            .sum()
            .rename(columns={"売上金額": "商品合計"})
        )
        order_by_order = (
            order_df.dropna(subset=["注文番号"])
            .groupby(["注文番号", "店舗名"], as_index=False)["売上金額"]
            .sum()
            .rename(columns={"売上金額": "税込金額"})
        )
        compared = product_by_order.merge(
            order_by_order,
            on=["注文番号", "店舗名"],
            how="outer",
        ).fillna(0)
        compared["差額"] = compared["商品合計"] - compared["税込金額"]
        mismatch_orders = compared[compared["差額"].abs() >= 0.5].sort_values(
            ["店舗名", "注文番号"]
        )

    return {
        "product_total": product_total,
        "order_total": order_total,
        "difference": difference,
        "matched": matched,
        "store_compare": store_compare,
        "mismatch_orders": mismatch_orders,
    }


def format_month_label(period_value):
    if pd.isna(period_value):
        return ""
    return f"{period_value.year}年{period_value.month}月"


def classify_store_group(store_name):
    name = sanitize_print_text(store_name)
    if "神宮寺" in name:
        return 0
    if "STAND" in name.upper():
        return 1
    return 2


def ordered_store_names(store_names):
    unique_names = sorted(
        {sanitize_print_text(name) for name in store_names if sanitize_print_text(name)}
    )
    return sorted(unique_names, key=lambda name: (classify_store_group(name), name))


def aggregate_sales_by_store(sales_df):
    summary = (
        sales_df.groupby("店舗名", as_index=False)
        .agg(売上金額=("売上金額", "sum"))
    )
    if summary.empty:
        return summary
    summary["店舗順"] = summary["店舗名"].map(classify_store_group)
    return summary.sort_values(["店舗順", "店舗名"]).drop(columns=["店舗順"])


def aggregate_sales_by_store_product(sales_df):
    summary = (
        sales_df.groupby(["店舗名", "商品名"], as_index=False)
        .agg(数量=("集計数量", "sum"), 売上金額=("売上金額", "sum"))
        .sort_values(["店舗名", "売上金額", "商品名"], ascending=[True, False, True])
    )
    return summary[is_nonzero_sales_amount(summary["売上金額"])].copy()


def aggregate_sales_by_store_day(sales_df):
    daily = (
        sales_df.dropna(subset=["集計日"])
        .groupby(["店舗名", "集計日"], as_index=False)
        .agg(売上金額=("売上金額", "sum"))
        .sort_values(["店舗名", "集計日"])
    )
    daily["日にち"] = daily["集計日"].map(
        lambda value: value.strftime("%m月%d日") if pd.notna(value) else ""
    )
    return daily[["店舗名", "日にち", "売上金額"]]


def build_store_product_rows(product_summary):
    rows = []
    for _, row in product_summary.sort_values(
        ["売上金額", "商品名"], ascending=[False, True]
    ).iterrows():
        product_name = html.escape(sanitize_print_text(row["商品名"]))
        quantity = html.escape(format_quantity(row["数量"]))
        amount = html.escape(format_amount(row["売上金額"]))
        rows.append(
            f"<tr><td>{product_name}</td><td>{quantity}</td><td>{amount}</td></tr>"
        )
    return rows


def build_store_day_rows(day_summary):
    rows = []
    for _, row in day_summary.iterrows():
        day_label = html.escape(sanitize_print_text(row["日にち"]))
        amount = html.escape(format_amount(row["売上金額"]))
        rows.append(f"<tr><td>{day_label}</td><td>{amount}</td></tr>")
    return rows


def build_store_detail_section(store_name, store_amount, product_summary, day_summary):
    product_rows = build_store_product_rows(product_summary)
    day_rows = build_store_day_rows(day_summary)
    product_body = (
        "\n".join(product_rows)
        if product_rows
        else '<tr><td colspan="3">データなし</td></tr>'
    )
    day_body = (
        "\n".join(day_rows)
        if day_rows
        else '<tr><td colspan="2">データなし</td></tr>'
    )
    store_label = html.escape(sanitize_print_text(store_name))
    amount_label = html.escape(format_amount(store_amount))
    return f"""
<section class="print-section store-detail-section">
    <h2>{store_label}</h2>
    <div class="print-section-meta">売上高: {amount_label}</div>
    <div class="sales-print-columns">
        <div>
            <h3>商品別売上（金額降順）</h3>
            <table class="print-table sales-product-table">
                <thead>
                    <tr>
                        <th>商品名</th>
                        <th>数量</th>
                        <th>金額</th>
                    </tr>
                </thead>
                <tbody>
                    {product_body}
                </tbody>
            </table>
        </div>
        <div>
            <h3>日にち別売上</h3>
            <table class="print-table sales-day-table">
                <thead>
                    <tr>
                        <th>日にち</th>
                        <th>金額</th>
                    </tr>
                </thead>
                <tbody>
                    {day_body}
                </tbody>
            </table>
        </div>
    </div>
</section>
""".strip()


def build_sales_print_report(
    period_label,
    store_summary,
    store_product_summary,
    store_day_summary,
    allow_extra_pages=False,
):
    total_amount = store_summary["売上金額"].sum()
    store_rows = []
    for _, row in store_summary.iterrows():
        store_name = html.escape(sanitize_print_text(row["店舗名"]))
        amount = html.escape(format_amount(row["売上金額"]))
        store_rows.append(f"<tr><td>{store_name}</td><td>{amount}</td></tr>")

    store_names = ordered_store_names(store_summary["店舗名"].tolist())
    detail_sections = []
    for store_name in store_names:
        store_amount_rows = store_summary[store_summary["店舗名"] == store_name]
        store_amount = (
            float(store_amount_rows["売上金額"].sum())
            if not store_amount_rows.empty
            else 0.0
        )
        product_summary = store_product_summary[
            store_product_summary["店舗名"] == store_name
        ]
        day_summary = store_day_summary[store_day_summary["店舗名"] == store_name]
        detail_sections.append(
            build_store_detail_section(
                store_name,
                store_amount,
                product_summary,
                day_summary,
            )
        )

    store_rows_html = "\n".join(store_rows)
    detail_sections_html = "\n".join(detail_sections)
    period_text = html.escape(sanitize_print_text(period_label))
    total_text = html.escape(format_amount(total_amount))
    layout_class = (
        "sales-print-multipage" if allow_extra_pages else "sales-print-fit"
    )

    return f"""
<div class="print-area sales-print-area {layout_class}">
    <div class="print-header">
        <h1>当月売上集計</h1>
        <div class="print-date">期間: {period_text}</div>
    </div>
    <div class="print-section-meta">
        店舗別・日にち別は「税込金額」。商品別は数量が2以上のとき数量×単価、それ以外は金額列を集計。
        0円の商品は除外。表示順は神宮寺店 → STAND です。
    </div>

    <section class="print-section sales-summary-section">
        <h2>期間中の店舗別売上高</h2>
        <table class="print-table sales-store-table">
            <thead>
                <tr>
                    <th>店舗名</th>
                    <th>売上高</th>
                </tr>
            </thead>
            <tbody>
                {store_rows_html}
            </tbody>
            <tfoot>
                <tr>
                    <th>合計</th>
                    <th>{total_text}</th>
                </tr>
            </tfoot>
        </table>
    </section>

    {detail_sections_html}
</div>
""".strip()


def get_japanese_font():
    font_paths = [
        Path(__file__).resolve().parent / "fonts" / "NotoSansJP-Regular.otf",
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
    selected_time_products=None,
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
        if selected_time_products:
            product_label = " / ".join(selected_time_products)
            time_section_meta = (
                f'<div class="print-section-meta">'
                f"対象商品: {html.escape(product_label)}"
                f"</div>"
            )
        else:
            time_section_meta = ""
        time_summary_table = dedent(
            f"""
            <section class="print-section">
                <h2>時間帯別 商品数量</h2>
                {time_section_meta}
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

        .print-section-meta {
            font-size: 13px;
            color: #374151;
            margin: -4px 0 10px;
            line-height: 1.45;
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

        .sales-print-area {
            max-width: 980px;
        }

        .sales-print-area .print-header h1 {
            font-size: 24px;
        }

        .sales-print-area .print-section {
            margin-top: 16px;
        }

        .sales-print-area .print-section h2 {
            font-size: 16px;
            margin: 0 0 6px;
        }

        .sales-print-area .print-table {
            font-size: 12px;
        }

        .sales-print-area .print-table th,
        .sales-print-area .print-table td {
            padding: 4px 8px;
        }

        .sales-print-area .print-table tfoot th {
            font-size: 13px;
        }

        .sales-summary-section .print-table {
            max-width: 420px;
        }

        .sales-print-columns {
            display: grid;
            grid-template-columns: 1.35fr 1fr;
            gap: 18px;
            align-items: start;
        }

        .store-detail-section {
            border-top: 1px solid #d1d5db;
            padding-top: 12px;
        }

        .store-detail-section h3 {
            font-size: 13px;
            line-height: 1.3;
            margin: 0 0 6px;
        }

        .sales-product-table td:nth-child(2),
        .sales-product-table th:nth-child(2),
        .sales-product-table td:nth-child(3),
        .sales-product-table th:nth-child(3),
        .sales-day-table td:nth-child(2),
        .sales-day-table th:nth-child(2) {
            text-align: right;
            white-space: nowrap;
        }

        .sales-product-table td:last-child,
        .sales-product-table th:last-child,
        .sales-day-table td:last-child,
        .sales-day-table th:last-child {
            width: 96px;
        }

        .sales-day-table td:first-child,
        .sales-day-table th:first-child {
            width: 72px;
            white-space: nowrap;
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

            .sales-print-area {
                width: 186mm !important;
                max-width: 186mm !important;
            }

            .sales-print-area .print-header {
                margin-bottom: 10px;
                padding-bottom: 8px;
            }

            .sales-print-area .print-header h1 {
                font-size: 18px !important;
            }

            .sales-print-area .print-date {
                font-size: 12px !important;
            }

            .sales-print-area .print-section {
                margin-top: 10px;
            }

            .sales-print-area .print-section h2 {
                font-size: 12px !important;
                margin-bottom: 4px !important;
            }

            .sales-print-area .print-table {
                font-size: 9.5px !important;
            }

            .sales-print-area .print-table th,
            .sales-print-area .print-table td {
                padding: 2px 4px !important;
            }

            .sales-print-columns {
                gap: 10px;
            }

            .print-area.sales-print-area.sales-print-multipage {
                position: absolute !important;
                left: 0 !important;
                top: 0 !important;
                transform: none !important;
                width: 186mm !important;
                max-width: 186mm !important;
                height: auto !important;
                overflow: visible !important;
                page-break-inside: auto !important;
                break-inside: auto !important;
            }

            .sales-print-multipage .store-detail-section {
                page-break-inside: auto;
                break-inside: auto;
            }

            .sales-print-multipage .print-table thead {
                display: table-header-group;
            }

            .sales-print-multipage .print-table tr {
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
menu = st.sidebar.radio(
    "表示する画面",
    ["アップロード", "集計", "当月売上", "注文票PDF"],
)

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
    selected_time_products = None
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
        available_time_products = sorted(
            selected_summary_df["商品名"].dropna().astype(str).unique().tolist()
        )
        if available_time_products:
            selected_time_products = st.multiselect(
                "時間帯別集計の商品",
                available_time_products,
                default=available_time_products,
                help="時間帯別の表に含める商品を選べます。",
            )
            if not selected_time_products:
                st.warning("時間帯別集計する商品を選択してください。")
                st.stop()
            time_target_df = selected_summary_df[
                selected_summary_df["商品名"].astype(str).isin(selected_time_products)
            ]
            time_product_summary = (
                time_target_df.dropna(subset=["商品名"])
                .groupby(["集計時刻", "商品名"], as_index=False)["集計数量"]
                .sum()
                .rename(columns={"集計時刻": "時刻", "集計数量": "数量"})
            )
            time_product_summary["_時刻順"] = time_product_summary["時刻"].map(
                time_sort_key
            )
            time_product_summary = time_product_summary.sort_values(
                ["_時刻順", "商品名"]
            ).drop(columns=["_時刻順"])
        else:
            st.info("選択した時間帯には商品データがありません。")

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
            selected_time_products=selected_time_products,
        ),
        unsafe_allow_html=True,
    )

elif menu == "当月売上":
    st.title("当月売上集計")
    if "df" not in st.session_state:
        st.info("先にアップロード画面でCSVファイルを読み込んでください。")
        st.stop()

    df = st.session_state.df
    required_sales_columns = ["お渡し日", "商品名", "数量", "金額", "店舗名"]
    missing_sales_columns = [
        column for column in required_sales_columns if column not in df.columns
    ]
    if missing_sales_columns:
        st.error(f"売上集計に必要な列が見つかりません: {', '.join(missing_sales_columns)}")
        st.stop()

    sales_df = prepare_sales_df(df)
    order_sales_df = filter_order_sales_df(sales_df)
    product_sales_df = filter_product_sales_df(sales_df)
    available_months = sorted(
        pd.concat([order_sales_df["集計月"], product_sales_df["集計月"]])
        .dropna()
        .unique()
    )
    if not available_months:
        st.warning("お渡し日として読み取れる日付がありません。")
        st.stop()

    today = date.today()
    current_month = pd.Period(today, freq="M")
    default_month = (
        current_month if current_month in available_months else available_months[-1]
    )
    month_options = [format_month_label(month) for month in available_months]
    default_month_label = format_month_label(default_month)
    selected_month_label = st.selectbox(
        "集計月",
        month_options,
        index=month_options.index(default_month_label),
        help="初期値は当月です。CSVに当月データがない場合は最新月を表示します。",
    )
    selected_month = available_months[month_options.index(selected_month_label)]
    month_order_df = order_sales_df[order_sales_df["集計月"] == selected_month].copy()
    month_product_df = product_sales_df[
        product_sales_df["集計月"] == selected_month
    ].copy()

    if month_order_df.empty and month_product_df.empty:
        st.warning(f"{selected_month_label}の売上データがありません。")
        st.stop()

    if month_order_df.empty:
        st.warning(
            "「税込金額」行がないため、店舗別・日にち別は商品行の金額で集計しています。"
        )
        amount_sales_df = month_product_df
    else:
        amount_sales_df = month_order_df

    store_summary = aggregate_sales_by_store(amount_sales_df)
    store_product_summary = aggregate_sales_by_store_product(month_product_df)
    store_day_summary = aggregate_sales_by_store_day(amount_sales_df)
    reconciliation = build_sales_amount_reconciliation(
        month_product_df,
        month_order_df,
    )

    if month_order_df.empty:
        st.info("税込金額行がないため、照合はスキップしています。")
    elif reconciliation["matched"]:
        st.success(
            "商品合計（数量×単価）と税込金額合計は一致しています: "
            f"{format_amount(reconciliation['order_total'])}"
        )
    else:
        st.error(
            "商品合計と税込金額が一致しません。 "
            f"商品合計 {format_amount(reconciliation['product_total'])} / "
            f"税込金額 {format_amount(reconciliation['order_total'])} / "
            f"差額 {format_amount(reconciliation['difference'])}"
        )
        if not reconciliation["mismatch_orders"].empty:
            st.caption("不一致の注文（先頭20件）")
            mismatch_preview = reconciliation["mismatch_orders"].head(20).copy()
            mismatch_preview["注文番号"] = mismatch_preview["注文番号"].map(
                format_integer_value
            )
            for column in ["商品合計", "税込金額", "差額"]:
                mismatch_preview[column] = mismatch_preview[column].map(format_amount)
            st.dataframe(mismatch_preview, use_container_width=True, hide_index=True)

    allow_extra_pages = st.checkbox(
        "A4に収まらないときはページを増やす",
        value=False,
        help="オフのときは1枚に収めます。商品が多くて切れるときはオンにしてください。",
    )
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
            A4で印刷
        </button>
        """,
        height=48,
    )
    st.html(
        build_sales_print_report(
            selected_month_label,
            store_summary,
            store_product_summary,
            store_day_summary,
            allow_extra_pages=allow_extra_pages,
        )
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
