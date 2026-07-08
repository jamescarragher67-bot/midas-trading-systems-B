//+------------------------------------------------------------------+
//| MidasOverlay.mq5                                                  |
//| Midas Capital — Visual mirror of Bot 1 & Bot 2 logic             |
//| Attach to XAUUSD M5 chart in MetaTrader 5                        |
//+------------------------------------------------------------------+
//
// ╔══════════════════════════════════════════════════════════════════╗
// ║  SYNC-LOCK — update the #define block below if Python changes   ║
// ╠═══════════════════════════════════════════════════════════════╤══╣
// ║  Python source                         Parameter          Value ║
// ╠═══════════════════════════════════════════════════════════════╪══╣
// ║  config/settings.py                    EMA_FAST              9  ║
// ║  config/settings.py                    EMA_MEDIUM           21  ║
// ║  config/settings.py                    EMA_TREND            50  ║
// ║  config/settings.py                    ATR_PERIOD           14  ║
// ║  config/settings.py                    MAX_SPREAD_POINTS    15  ║
// ║  main_combined.py (_BOT2)              max_spread_points    20  ║
// ║  main_combined.py                      MAX_COMBINED_TRADES   6  ║
// ║  main_combined.py                      MAGIC             10001  ║
// ║  strategy/atr_expansion.py             ATR_EXPANSION_MULT  1.2  ║
// ║  strategy/atr_expansion.py             ATR_MA_PERIOD        20  ║
// ║  strategy/volatility_metrics.py        ATR_MA50_PERIOD      50  ║
// ║  strategy/volatility_metrics.py        STDDEV_PERIOD        20  ║
// ║  strategy/volatility_metrics.py        VOV com=19 → alpha 1/20  ║
// ║  strategy/volatility_metrics.py        VOV_MA_PERIOD        20  ║
// ║  strategy/volatility_metrics.py        HL_MA_PERIOD         20  ║
// ║  strategy/regime_classifier.py         C_ATR_RATIO_MIN    1.50  ║
// ║  strategy/regime_classifier.py         C_VOV_RATIO_MIN    1.30  ║
// ║  strategy/regime_classifier.py         A_ATR_RATIO_MAX    0.80  ║
// ║  strategy/regime_classifier.py         A_STDDEV_RATIO_MAX 0.80  ║
// ║  strategy/regime_classifier.py         A_HL_COMP_MAX      0.75  ║
// ║  strategy/regime_classifier.py         B_ATR_RATIO_MIN    0.80  ║
// ║  strategy/regime_classifier.py         B_ATR_RATIO_MAX    1.50  ║
// ║  strategy/regime_classifier.py         B_STDDEV_RATIO_MIN 0.80  ║
// ║  strategy/regime_classifier.py         B_STDDEV_RATIO_MAX 1.50  ║
// ║  strategy/transition_detector.py       BC_ATR_RATIO_MIN   1.50  ║
// ║  strategy/transition_detector.py       BC_VOV_RATIO_MIN   1.30  ║
// ║  strategy/transition_detector.py       BC_WICK_RATIO_MIN  0.60  ║
// ║  config/settings.py ALLOWED_SESSIONS:                           ║
// ║    Active  : 00:00-14:59 UTC, 20:00-23:59 UTC                  ║
// ║    Blocked : 15:00-19:59 UTC                                    ║
// ╚══════════════════════════════════════════════════════════════════╝

#property copyright   "Midas Capital"
#property description "Midas Bot 1 / Bot 2 visual overlay — EMA Stack, ATR Expansion, Prev Day Structure, Volatility Regime"
#property version     "1.00"

#property indicator_chart_window
#property indicator_buffers 3
#property indicator_plots   3

//── EMA9 ──────────────────────────────────────────────────────────
#property indicator_label1 "EMA9"
#property indicator_type1  DRAW_LINE
#property indicator_color1 clrDodgerBlue
#property indicator_width1 1
#property indicator_style1 STYLE_SOLID

//── EMA21 ─────────────────────────────────────────────────────────
#property indicator_label2 "EMA21"
#property indicator_type2  DRAW_LINE
#property indicator_color2 clrOrange
#property indicator_width2 1
#property indicator_style2 STYLE_SOLID

//── EMA50 ─────────────────────────────────────────────────────────
#property indicator_label3 "EMA50"
#property indicator_type3  DRAW_LINE
#property indicator_color3 clrMediumPurple
#property indicator_width3 2
#property indicator_style3 STYLE_SOLID

//+------------------------------------------------------------------+
//  INPUT PARAMETERS
//+------------------------------------------------------------------+
input int  InpPanelX = 10;     // Panel: pixels from left edge
input int  InpPanelY = 30;     // Panel: pixels from top
input int  InpMagic  = 10001;  // Bot 1/2 magic number (trades-today count)

//+------------------------------------------------------------------+
//  SYNC-LOCK CONSTANTS — edit here if Python config changes
//+------------------------------------------------------------------+
#define EMA_FAST_P       9
#define EMA_SLOW_P       21
#define EMA_TREND_P      50
#define ATR_P            14
#define ATR_EXPAND_MULT  1.2
#define ATR_MA_P         20
#define ATR_MA50_P       50
#define STDDEV_P         20
#define HL_MA_P          20
#define VOV_ALPHA        0.05    // 1/20 (com=19)
#define VOV_MA_P         20
#define SPREAD_B1_MAX    15.0
#define SPREAD_B2_MAX    20.0
#define MAX_TRADES       6
#define C_ATR_MIN        1.50
#define C_VOV_MIN        1.30
#define A_ATR_MAX        0.80
#define A_STD_MAX        0.80
#define A_HL_MAX         0.75
#define B_ATR_MIN        0.80
#define B_ATR_MAX        1.50
#define B_STD_MIN        0.80
#define B_STD_MAX        1.50
#define BC_ATR_MIN       1.50
#define BC_VOV_MIN       1.30
#define BC_WICK_MIN      0.60

//── Panel object name prefix and font ─────────────────────────────
#define PFX "MIDAS_"
#define FONT "Consolas"
#define FONT_SZ 9

//── Colors ────────────────────────────────────────────────────────
#define CLR_BUY    clrLimeGreen
#define CLR_SELL   clrTomato
#define CLR_NEUTRAL clrDimGray
#define CLR_HEAD   clrGold
#define CLR_TEXT   clrSilver
#define CLR_SUB    clrDimGray
#define CLR_REG_A  clrCornflowerBlue
#define CLR_REG_B  clrMediumSeaGreen
#define CLR_REG_C  clrOrange
#define CLR_REG_U  clrDimGray
#define CLR_FLASH  clrYellow
#define CLR_BG     C'16,18,22'
#define CLR_BORDER C'48,52,60'

//+------------------------------------------------------------------+
//  INDICATOR BUFFERS
//+------------------------------------------------------------------+
double Buf_EMA9[];
double Buf_EMA21[];
double Buf_EMA50[];

//+------------------------------------------------------------------+
//  GLOBAL STATE
//+------------------------------------------------------------------+
int    g_h9, g_h21, g_h50, g_hatr;
string g_prev_regime = "UNKNOWN";

//+------------------------------------------------------------------+
//  ONINIT
//+------------------------------------------------------------------+
int OnInit()
{
    //── Indicator buffers
    SetIndexBuffer(0, Buf_EMA9,  INDICATOR_DATA);
    SetIndexBuffer(1, Buf_EMA21, INDICATOR_DATA);
    SetIndexBuffer(2, Buf_EMA50, INDICATOR_DATA);
    PlotIndexSetDouble(0, PLOT_EMPTY_VALUE, 0.0);
    PlotIndexSetDouble(1, PLOT_EMPTY_VALUE, 0.0);
    PlotIndexSetDouble(2, PLOT_EMPTY_VALUE, 0.0);
    IndicatorSetInteger(INDICATOR_DIGITS, _Digits);

    //── Sub-indicator handles
    g_h9   = iMA(_Symbol, _Period, EMA_FAST_P,  0, MODE_EMA, PRICE_CLOSE);
    g_h21  = iMA(_Symbol, _Period, EMA_SLOW_P,  0, MODE_EMA, PRICE_CLOSE);
    g_h50  = iMA(_Symbol, _Period, EMA_TREND_P, 0, MODE_EMA, PRICE_CLOSE);
    g_hatr = iATR(_Symbol, _Period, ATR_P);

    if (g_h9  == INVALID_HANDLE || g_h21 == INVALID_HANDLE ||
        g_h50 == INVALID_HANDLE || g_hatr == INVALID_HANDLE) {
        Print("MidasOverlay: failed to create indicator handles");
        return INIT_FAILED;
    }

    BuildPanel();
    ChartRedraw(0);
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//  ONDEINIT
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    ObjectsDeleteAll(0, PFX);
    IndicatorRelease(g_h9);
    IndicatorRelease(g_h21);
    IndicatorRelease(g_h50);
    IndicatorRelease(g_hatr);
    ChartRedraw(0);
}

//+------------------------------------------------------------------+
//  ONCALCULATE
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double   &open[],
                const double   &high[],
                const double   &low[],
                const double   &close[],
                const long     &tick_volume[],
                const long     &volume[],
                const int      &spread[])
{
    int min_bars = ATR_MA50_P + STDDEV_P + VOV_MA_P + 10;
    if (rates_total < min_bars) return 0;

    //── Fill EMA plot buffers ──────────────────────────────────────
    int to_copy = (prev_calculated == 0) ? rates_total : (rates_total - prev_calculated + 1);

    double tmp[];
    ArraySetAsSeries(tmp, true);

    if (CopyBuffer(g_h9, 0, 0, to_copy, tmp) > 0)
        for (int i = rates_total - to_copy; i < rates_total; i++)
            Buf_EMA9[i] = tmp[rates_total - 1 - i];

    if (CopyBuffer(g_h21, 0, 0, to_copy, tmp) > 0)
        for (int i = rates_total - to_copy; i < rates_total; i++)
            Buf_EMA21[i] = tmp[rates_total - 1 - i];

    if (CopyBuffer(g_h50, 0, 0, to_copy, tmp) > 0)
        for (int i = rates_total - to_copy; i < rates_total; i++)
            Buf_EMA50[i] = tmp[rates_total - 1 - i];

    //── Panel update every tick ────────────────────────────────────
    UpdatePanel();

    return rates_total;
}

//+------------------------------------------------------------------+
//  UPDATE PANEL — called every tick
//+------------------------------------------------------------------+
void UpdatePanel()
{
    int x0 = InpPanelX;
    int y0 = InpPanelY;
    int xv = x0 + 130;   // value column x
    int xd = x0 + 195;   // detail column x

    //── UTC offset (server → UTC): utc = server - offset
    int utc_offset = (int)(TimeCurrent() - TimeGMT());

    // ── ── ── BOT 1: EMA STACK ────────────────────────────────────
    // Python ema_stack.py uses df.iloc[-2] = last COMPLETED bar → shift 1
    double e9  = GetBufVal(g_h9,   1);
    double e21 = GetBufVal(g_h21,  1);
    double e50 = GetBufVal(g_h50,  1);
    double cl1 = iClose(_Symbol, _Period, 1);

    string ema_txt = "NONE"; color ema_clr = CLR_NEUTRAL; int ema_vote = 0;
    if (e9 > e21 && e21 > e50 && cl1 > e50) { ema_txt = "BUY";  ema_clr = CLR_BUY;  ema_vote =  1; }
    if (e9 < e21 && e21 < e50 && cl1 < e50) { ema_txt = "SELL"; ema_clr = CLR_SELL; ema_vote = -1; }
    Lbl(PFX+"v_ema", ema_txt, xv, y0+28, ema_clr);

    // ── ── ── BOT 1: ATR EXPANSION ───────────────────────────────
    // Python: atr_series excludes current bar (iloc[:-1]) → all completed → shift 1+
    // Direction: net of close[-2] - close[-4] = iClose(1) - iClose(3)
    double atr_vals[22];
    bool   atr_data_ok = true;
    for (int k = 0; k < 22; k++) {
        atr_vals[k] = GetBufVal(g_hatr, k + 1);
        if (atr_vals[k] <= 0) { atr_data_ok = false; break; }
    }

    string atr_txt = "NONE"; color atr_clr = CLR_NEUTRAL; int atr_vote = 0;
    string atr_prog = "";

    if (atr_data_ok) {
        double cur_atr = atr_vals[0];
        double ma20    = 0.0;
        for (int k = 0; k < ATR_MA_P; k++) ma20 += atr_vals[k];
        ma20 /= ATR_MA_P;

        double pct = (ma20 > 0) ? MathMin(100.0, cur_atr / (ma20 * ATR_EXPAND_MULT) * 100.0) : 0.0;
        atr_prog = StringFormat("%.0f%%→thresh", pct);

        if (cur_atr >= ma20 * ATR_EXPAND_MULT) {
            double net = iClose(_Symbol, _Period, 1) - iClose(_Symbol, _Period, 3);
            if (net > 0)      { atr_txt = "BUY";  atr_clr = CLR_BUY;  atr_vote =  1; }
            else if (net < 0) { atr_txt = "SELL"; atr_clr = CLR_SELL; atr_vote = -1; }
            else              { atr_prog = "flat"; }
        }
    }
    Lbl(PFX+"v_atr",  atr_txt,  xv,   y0+42, atr_clr);
    Lbl(PFX+"d_atr",  atr_prog, xd,   y0+42, CLR_TEXT);

    // ── ── ── BOT 1: PREV DAY STRUCTURE ──────────────────────────
    // Python: BUY if current_close > PDH, SELL if < PDL
    // Uses df.iloc[-1] close = live current close → iClose shift 0
    double pdh = 0.0, pdl = 0.0;
    bool   pds_ok = GetPDHL(pdh, pdl, utc_offset);

    string pds_txt = "NONE"; color pds_clr = CLR_NEUTRAL; int pds_vote = 0;
    string pds_dist = "";

    if (pds_ok && pdh > 0 && pdl > 0) {
        double price = iClose(_Symbol, _Period, 0);
        double pt    = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
        if (price > pdh) {
            pds_txt = "BUY";  pds_clr = CLR_BUY;  pds_vote =  1;
            pds_dist = StringFormat("+%.1fpt vs PDH", (price - pdh) / pt);
        } else if (price < pdl) {
            pds_txt = "SELL"; pds_clr = CLR_SELL; pds_vote = -1;
            pds_dist = StringFormat("-%.1fpt vs PDL", (pdl - price) / pt);
        } else {
            pds_dist = StringFormat("PDH+%.0f  PDL-%.0f", (pdh - price)/pt, (price - pdl)/pt);
        }
    }
    Lbl(PFX+"v_pds",  pds_txt,  xv,   y0+56, pds_clr);
    Lbl(PFX+"d_pds",  pds_dist, xd,   y0+56, CLR_TEXT);

    // PDH / PDL horizontal lines
    if (pds_ok && pdh > 0) {
        HLine(PFX+"pdh", pdh, "PDH  " + DoubleToString(pdh, _Digits), clrCornflowerBlue);
        HLine(PFX+"pdl", pdl, "PDL  " + DoubleToString(pdl, _Digits), clrLightCoral);
    }

    // ── ── ── BOT 1: COMBINED BIAS ───────────────────────────────
    // 3/3 unanimous — exactly what main_combined.py requires
    int buy_n  = (int)(ema_vote > 0) + (int)(atr_vote > 0) + (int)(pds_vote > 0);
    int sell_n = (int)(ema_vote < 0) + (int)(atr_vote < 0) + (int)(pds_vote < 0);

    string comb_txt; color comb_clr; string comb_det;
    if      (buy_n  == 3) { comb_txt = "BUY";  comb_clr = CLR_BUY;  comb_det = "3/3 unanimous"; }
    else if (sell_n == 3) { comb_txt = "SELL"; comb_clr = CLR_SELL; comb_det = "3/3 unanimous"; }
    else                  { comb_txt = "NONE"; comb_clr = CLR_NEUTRAL;
                            comb_det = StringFormat("%d/3 BUY  %d/3 SELL", buy_n, sell_n); }
    Lbl(PFX+"v_comb", comb_txt, xv, y0+70, comb_clr);
    Lbl(PFX+"d_comb", comb_det, xd, y0+70, CLR_TEXT);

    // ── ── ── BOT 2: REGIME METRICS ──────────────────────────────
    double atr_ratio = 1.0, std_ratio = 1.0, hl_comp = 1.0;
    double vov_ratio = 1.0, wick_ratio = 1.0;
    bool   reg_ok = RegimeMetrics(atr_ratio, std_ratio, hl_comp, vov_ratio, wick_ratio);

    string regime = "UNKNOWN";
    color  reg_clr = CLR_REG_U;
    string reg_lbl = "UNKNOWN";

    if (reg_ok) {
        regime = ClassifyRegime(atr_ratio, std_ratio, hl_comp, vov_ratio);

        bool bc = (g_prev_regime == "B" || g_prev_regime == "UNKNOWN") &&
                  regime == "C" &&
                  atr_ratio  > BC_ATR_MIN &&
                  vov_ratio  > BC_VOV_MIN &&
                  wick_ratio > BC_WICK_MIN;

        if      (regime == "A") { reg_lbl = "A  (compression)"; reg_clr = CLR_REG_A; }
        else if (regime == "B") { reg_lbl = "B  (expansion)";   reg_clr = CLR_REG_B; }
        else if (regime == "C") { reg_lbl = bc ? "C  <<< B→C SIGNAL !!!" : "C  (exhaustion)";
                                  reg_clr = bc ? CLR_FLASH : CLR_REG_C; }
        else                    { reg_lbl = "UNKNOWN"; }

        g_prev_regime = regime;
    }

    Lbl(PFX+"v_reg",  reg_lbl,                           xv,   y0+112, reg_clr);
    Lbl(PFX+"v_ar",   StringFormat("%.3f", atr_ratio),   xv,   y0+126, CLR_TEXT);
    Lbl(PFX+"v_sr",   StringFormat("%.3f", std_ratio),   xv,   y0+140, CLR_TEXT);
    Lbl(PFX+"v_hl",   StringFormat("%.3f", hl_comp),     xv,   y0+154, CLR_TEXT);
    Lbl(PFX+"v_vov",  StringFormat("%.3f", vov_ratio),   xv,   y0+168, CLR_TEXT);

    // ── ── ── SHARED INFO ─────────────────────────────────────────
    // Session filter: active 00:00-14:59 UTC and 20:00-23:59 UTC
    MqlDateTime utc_dt;
    TimeToStruct(TimeGMT(), utc_dt);
    bool in_sess = (utc_dt.hour >= 0  && utc_dt.hour <= 14) ||
                   (utc_dt.hour >= 20 && utc_dt.hour <= 23);
    string sess_txt = in_sess ? "ACTIVE  (00:00-14:59 / 20:00-23:59 UTC)"
                               : "BLOCKED (15:00-19:59 UTC)";
    Lbl(PFX+"v_sess", sess_txt, xv, y0+212, in_sess ? CLR_BUY : CLR_SELL);

    // Spread
    MqlTick tk;
    if (SymbolInfoTick(_Symbol, tk)) {
        double pt   = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
        double sp   = (pt > 0) ? (tk.ask - tk.bid) / pt : 0.0;
        bool   ok1  = (sp <= SPREAD_B1_MAX);
        bool   ok2  = (sp <= SPREAD_B2_MAX);
        string stxt = StringFormat("%.0fpt  %s  %s  (lim %d/%d)",
                                   sp,
                                   ok1 ? "OK-B1" : "WIDE-B1",
                                   ok2 ? "OK-B2" : "WIDE-B2",
                                   (int)SPREAD_B1_MAX, (int)SPREAD_B2_MAX);
        Lbl(PFX+"v_spread", stxt, xv, y0+226, ok1 ? CLR_BUY : CLR_SELL);
    }

    // Trades today (Bot 1/2 entries with magic 10001)
    int cnt = TradesToday();
    string t_txt = StringFormat("%d / %d", cnt, MAX_TRADES);
    Lbl(PFX+"v_trades", t_txt, xv, y0+240, cnt >= MAX_TRADES ? CLR_SELL : CLR_BUY);

    ChartRedraw(0);
}

//+------------------------------------------------------------------+
//  Get a single value from a sub-indicator handle at given shift
//+------------------------------------------------------------------+
double GetBufVal(int handle, int shift)
{
    double buf[1];
    if (CopyBuffer(handle, 0, shift, 1, buf) <= 0) return 0.0;
    return buf[0];
}

//+------------------------------------------------------------------+
//  GetPDHL — previous UTC calendar day High and Low
//  Python prev_day_structure.py: prev_date bars where index.date == prev_date
//+------------------------------------------------------------------+
bool GetPDHL(double &pdh, double &pdl, int utc_offset_secs)
{
    pdh = 0.0; pdl = 1e9;

    MqlDateTime utc_dt;
    TimeToStruct(TimeGMT(), utc_dt);
    utc_dt.hour = 0; utc_dt.min = 0; utc_dt.sec = 0;
    datetime today_utc     = StructToTime(utc_dt);
    datetime yday_utc_s    = today_utc - 86400;
    datetime yday_utc_e    = today_utc - 1;

    // Convert UTC range to server-time range for bar lookup
    datetime sv_start = yday_utc_s + utc_offset_secs;
    datetime sv_end   = yday_utc_e + utc_offset_secs;

    bool found = false;
    for (int k = 1; k <= 600; k++) {
        datetime bt = iTime(_Symbol, _Period, k);
        if (bt == 0) break;
        if (bt > sv_end)   continue;
        if (bt < sv_start) break;
        double h = iHigh(_Symbol, _Period, k);
        double l = iLow (_Symbol, _Period, k);
        if (h > pdh) pdh = h;
        if (l < pdl) pdl = l;
        found = true;
    }
    if (!found) { pdh = 0.0; pdl = 0.0; }
    return found;
}

//+------------------------------------------------------------------+
//  RegimeMetrics — mirrors strategy/volatility_metrics.py exactly
//
//  All ratios relative to rolling means so thresholds are price-agnostic.
//  ATR:   EWM com=13 (alpha=1/14) = iATR period 14  ✓
//  VoV:   EWM of |diff(ATR)| with com=19 (alpha=1/20)
//  Std:   rolling 20-bar std of log-returns / 50-bar mean of that std
//  HL:    current bar HL / 20-bar mean HL
//+------------------------------------------------------------------+
bool RegimeMetrics(double &atr_ratio, double &std_ratio,
                   double &hl_comp,   double &vov_ratio,
                   double &wick_ratio)
{
    atr_ratio = std_ratio = hl_comp = vov_ratio = wick_ratio = 1.0;

    int need = ATR_MA50_P + STDDEV_P + VOV_MA_P + 5;
    if (Bars(_Symbol, _Period) < need) return false;

    // ── ATR ratio: ATR(1) / mean(ATR[1..50])
    double cur_atr = GetBufVal(g_hatr, 1);
    if (cur_atr <= 0.0) return false;
    double ma50 = 0.0;
    for (int k = 1; k <= ATR_MA50_P; k++) {
        double v = GetBufVal(g_hatr, k);
        if (v <= 0.0) return false;
        ma50 += v;
    }
    ma50 /= ATR_MA50_P;
    atr_ratio = (ma50 > 0.0) ? cur_atr / ma50 : 1.0;

    // ── H/L compression: bar[1] HL / mean(HL[1..20])
    double hl_cur  = iHigh(_Symbol, _Period, 1) - iLow(_Symbol, _Period, 1);
    double hl_ma20 = 0.0;
    for (int k = 1; k <= HL_MA_P; k++)
        hl_ma20 += iHigh(_Symbol, _Period, k) - iLow(_Symbol, _Period, k);
    hl_ma20 /= HL_MA_P;
    hl_comp = (hl_ma20 > 0.0) ? hl_cur / hl_ma20 : 1.0;

    // ── Wick ratio: current bar (shift 1)
    double cb  = MathAbs(iClose(_Symbol, _Period, 1) - iOpen(_Symbol, _Period, 1));
    double chl = iHigh(_Symbol, _Period, 1) - iLow(_Symbol, _Period, 1);
    wick_ratio = (chl > 0.0) ? (chl - cb) / chl : 0.0;

    // ── StdDev of log returns
    // Need STDDEV_P + ATR_MA50_P log-returns = 70 returns → 71 closes (shifts 1..71)
    int   n_lr    = STDDEV_P + ATR_MA50_P;   // 70
    int   n_cl    = n_lr + 1;                 // 71
    double cls[];
    ArrayResize(cls, n_cl);
    // cls[0]=oldest (shift n_cl), cls[n_cl-1]=most recent completed (shift 1)
    for (int k = 0; k < n_cl; k++)
        cls[n_cl - 1 - k] = iClose(_Symbol, _Period, k + 1);

    double lr[];
    ArrayResize(lr, n_lr);
    for (int k = 0; k < n_lr; k++) {
        if (cls[k] > 0.0 && cls[k + 1] > 0.0)
            lr[k] = MathLog(cls[k + 1] / cls[k]);
        else
            lr[k] = 0.0;
    }

    // Current 20-bar stddev (last 20 log returns)
    double m20 = 0.0;
    for (int k = n_lr - STDDEV_P; k < n_lr; k++) m20 += lr[k];
    m20 /= STDDEV_P;
    double v20 = 0.0;
    for (int k = n_lr - STDDEV_P; k < n_lr; k++) { double d = lr[k] - m20; v20 += d * d; }
    double stddev_cur = MathSqrt(v20 / STDDEV_P);

    // 50-sample rolling std mean
    int n_roll = ATR_MA50_P;
    double std_sum = 0.0;
    for (int s = 0; s < n_roll; s++) {
        int st = n_lr - STDDEV_P - s;
        if (st < 0) break;
        double m = 0.0;
        for (int k = st; k < st + STDDEV_P; k++) m += lr[k];
        m /= STDDEV_P;
        double v = 0.0;
        for (int k = st; k < st + STDDEV_P; k++) { double d = lr[k] - m; v += d * d; }
        std_sum += MathSqrt(v / STDDEV_P);
    }
    double std_ma50 = std_sum / n_roll;
    std_ratio = (std_ma50 > 0.0) ? stddev_cur / std_ma50 : 1.0;

    // ── VoV: EWM(alpha=0.05) of |diff(ATR)|, then 20-bar mean of that EWM
    // Build ATR sequence oldest→newest: index 0 = oldest (shift vov_len), last = shift 1
    int    vov_len = ATR_MA50_P + VOV_MA_P + 5;  // 75
    double atr_sq[];
    ArrayResize(atr_sq, vov_len + 1);
    for (int k = 0; k <= vov_len; k++)
        atr_sq[vov_len - k] = GetBufVal(g_hatr, k + 1);

    double vov_sq[];
    ArrayResize(vov_sq, vov_len);
    vov_sq[0] = MathAbs(atr_sq[1] - atr_sq[0]);
    for (int k = 1; k < vov_len; k++) {
        double diff = MathAbs(atr_sq[k + 1] - atr_sq[k]);
        vov_sq[k]   = VOV_ALPHA * diff + (1.0 - VOV_ALPHA) * vov_sq[k - 1];
    }
    double vov_cur = vov_sq[vov_len - 1];
    double vov_ma  = 0.0;
    for (int k = vov_len - VOV_MA_P; k < vov_len; k++) vov_ma += vov_sq[k];
    vov_ma /= VOV_MA_P;
    vov_ratio = (vov_ma > 0.0) ? vov_cur / vov_ma : 1.0;

    return true;
}

//+------------------------------------------------------------------+
//  ClassifyRegime — mirrors strategy/regime_classifier.py exactly
//  Priority: C > A > B > UNKNOWN
//+------------------------------------------------------------------+
string ClassifyRegime(double ar, double sr, double hl, double vr)
{
    if (ar > C_ATR_MIN  && vr > C_VOV_MIN)                     return "C";
    if (ar < A_ATR_MAX  && sr < A_STD_MAX && hl < A_HL_MAX)    return "A";
    if (ar >= B_ATR_MIN && ar <= B_ATR_MAX &&
        sr >= B_STD_MIN && sr <= B_STD_MAX)                     return "B";
    return "UNKNOWN";
}

//+------------------------------------------------------------------+
//  TradesToday — count Bot 1/2 deal entries (DEAL_ENTRY_IN) today
//+------------------------------------------------------------------+
int TradesToday()
{
    MqlDateTime sv_dt;
    TimeToStruct(TimeCurrent(), sv_dt);
    sv_dt.hour = 0; sv_dt.min = 0; sv_dt.sec = 0;
    datetime day_start = StructToTime(sv_dt);

    if (!HistorySelect(day_start, TimeCurrent())) return 0;
    int count = 0;
    int total = HistoryDealsTotal();
    for (int k = 0; k < total; k++) {
        ulong ticket = HistoryDealGetTicket(k);
        if (HistoryDealGetInteger(ticket, DEAL_MAGIC) == InpMagic &&
            HistoryDealGetInteger(ticket, DEAL_ENTRY) == DEAL_ENTRY_IN)
            count++;
    }
    return count;
}

//+------------------------------------------------------------------+
//  Lbl — create or update a text label object
//+------------------------------------------------------------------+
void Lbl(string name, string text, int x, int y, color clr, int sz = FONT_SZ)
{
    if (ObjectFind(0, name) < 0) {
        ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
        ObjectSetString( 0, name, OBJPROP_FONT,       FONT);
        ObjectSetInteger(0, name, OBJPROP_CORNER,     CORNER_LEFT_UPPER);
        ObjectSetInteger(0, name, OBJPROP_ANCHOR,     ANCHOR_LEFT_UPPER);
        ObjectSetInteger(0, name, OBJPROP_BACK,       false);
        ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
        ObjectSetInteger(0, name, OBJPROP_HIDDEN,     true);
    }
    ObjectSetString( 0, name, OBJPROP_TEXT,      text);
    ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
    ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
    ObjectSetInteger(0, name, OBJPROP_COLOR,     clr);
    ObjectSetInteger(0, name, OBJPROP_FONTSIZE,  sz);
}

//+------------------------------------------------------------------+
//  HLine — create or update a dashed horizontal price line
//+------------------------------------------------------------------+
void HLine(string name, double price, string tooltip, color clr)
{
    if (ObjectFind(0, name) < 0) {
        ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
        ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
        ObjectSetInteger(0, name, OBJPROP_HIDDEN,     true);
        ObjectSetInteger(0, name, OBJPROP_WIDTH,      1);
        ObjectSetInteger(0, name, OBJPROP_STYLE,      STYLE_DASH);
    }
    ObjectSetDouble( 0, name, OBJPROP_PRICE, price);
    ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
    ObjectSetString( 0, name, OBJPROP_TEXT,  tooltip);
}

//+------------------------------------------------------------------+
//  BuildPanel — create background rect and all static text labels
//  Called once from OnInit. Value labels are created on first tick
//  by the Lbl() helper (creates if not found).
//+------------------------------------------------------------------+
void BuildPanel()
{
    int x0 = InpPanelX;
    int y0 = InpPanelY;

    //── Background rectangle
    if (ObjectFind(0, PFX+"bg") < 0)
        ObjectCreate(0, PFX+"bg", OBJ_RECTANGLE_LABEL, 0, 0, 0);
    ObjectSetInteger(0, PFX+"bg", OBJPROP_CORNER,      CORNER_LEFT_UPPER);
    ObjectSetInteger(0, PFX+"bg", OBJPROP_XDISTANCE,   x0 - 6);
    ObjectSetInteger(0, PFX+"bg", OBJPROP_YDISTANCE,   y0 - 6);
    ObjectSetInteger(0, PFX+"bg", OBJPROP_XSIZE,       430);
    ObjectSetInteger(0, PFX+"bg", OBJPROP_YSIZE,       270);
    ObjectSetInteger(0, PFX+"bg", OBJPROP_BGCOLOR,     CLR_BG);
    ObjectSetInteger(0, PFX+"bg", OBJPROP_BORDER_TYPE, BORDER_FLAT);
    ObjectSetInteger(0, PFX+"bg", OBJPROP_COLOR,       CLR_BORDER);
    ObjectSetInteger(0, PFX+"bg", OBJPROP_SELECTABLE,  false);
    ObjectSetInteger(0, PFX+"bg", OBJPROP_BACK,        false);

    //── Title
    Lbl(PFX+"ttl",  "MIDAS OVERLAY", x0, y0, CLR_HEAD, 9);

    //── Bot 1 header
    Lbl(PFX+"h_b1", "─ BOT 1: DAILY BIAS ──────────────────────────────────────", x0, y0+14, CLR_BORDER);

    //── Bot 1 row labels (static left column)
    Lbl(PFX+"l_ema",  "EMA Stack:    ", x0, y0+28,  CLR_TEXT);
    Lbl(PFX+"l_atr",  "ATR Expansion:", x0, y0+42,  CLR_TEXT);
    Lbl(PFX+"l_pds",  "Prev Day Str: ", x0, y0+56,  CLR_TEXT);
    Lbl(PFX+"l_comb", "COMBINED:     ", x0, y0+70,  CLR_TEXT);

    //── Bot 2 header
    Lbl(PFX+"h_b2",   "─ BOT 2: REGIME ──────────────────────────────────────────", x0, y0+98,  CLR_BORDER);

    //── Bot 2 row labels
    Lbl(PFX+"l_reg",  "Regime:       ", x0, y0+112, CLR_TEXT);
    Lbl(PFX+"l_ar",   "ATR ratio:    ", x0, y0+126, CLR_TEXT);
    Lbl(PFX+"l_sr",   "StdDev ratio: ", x0, y0+140, CLR_TEXT);
    Lbl(PFX+"l_hl",   "H/L compress: ", x0, y0+154, CLR_TEXT);
    Lbl(PFX+"l_vov",  "VoV ratio:    ", x0, y0+168, CLR_TEXT);

    //── Shared header
    Lbl(PFX+"h_sh",   "─ SHARED ─────────────────────────────────────────────────", x0, y0+196, CLR_BORDER);

    //── Shared row labels
    Lbl(PFX+"l_sess",   "Session:     ", x0, y0+212, CLR_TEXT);
    Lbl(PFX+"l_spread", "Spread:      ", x0, y0+226, CLR_TEXT);
    Lbl(PFX+"l_trades", "Trades today:", x0, y0+240, CLR_TEXT);
}
