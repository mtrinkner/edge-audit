#!/usr/bin/env Rscript
# =============================================================================
# R/figures.R — the five charts that carry the argument
#
#   Rscript R/figures.R
#
# Each chart exists to answer one question a reader will have, in the order they
# will ask it. Charts that only decorate are not here.
#
#   1  equity curve vs the passive benchmark  — did it beat buy and hold?
#   2  bootstrap null of the best excess      — is the edge distinguishable from luck?
#   3  per-fold excess return                 — does it hold up across regimes?
#   4  cost waterfall                         — where did the gross edge go?
#   5  permutation importance                 — what is the model actually using?
# =============================================================================

suppressPackageStartupMessages({
  library(DBI); library(RSQLite); library(dplyr); library(tidyr); library(ggplot2)
})

find_root <- function() {
  for (cand in c(".", "..")) {
    if (file.exists(file.path(cand, "data", "warehouse.db"))) return(cand)
  }
  stop("run this from the trading-bot directory")
}
ROOT <- find_root()
FIG <- file.path(ROOT, "reports", "figures")
dir.create(FIG, recursive = TRUE, showWarnings = FALSE)

con <- dbConnect(SQLite(), file.path(ROOT, "data", "warehouse.db"))
on.exit(dbDisconnect(con), add = TRUE)

# A single theme so every figure reads as one document.
theme_set(theme_minimal(base_size = 11) +
  theme(panel.grid.minor = element_blank(),
        plot.title = element_text(face = "bold", size = 13),
        plot.subtitle = element_text(colour = "grey35", size = 10),
        plot.caption = element_text(colour = "grey45", size = 8, hjust = 0),
        legend.position = "top", legend.title = element_blank()))
PAL <- c(strategy = "#1F3864", benchmark = "#C00000", neutral = "grey55")
save_fig <- function(p, name, w = 9, h = 5.2) {
  ggsave(file.path(FIG, name), p, width = w, height = h, dpi = 160)
  cat("  wrote", name, "\n")
}

cat("building figures\n")

# ------------------------------------------------- 1  equity vs buy and hold
bt <- dbGetQuery(con, "SELECT MAX(bt_id) id FROM backtest_runs")$id
eq <- dbGetQuery(con, sprintf(
  "SELECT dt, equity FROM backtest_equity WHERE bt_id = %d ORDER BY dt", bt))
spy <- dbGetQuery(con, sprintf("
  SELECT dt, adj_close FROM bars WHERE symbol='SPY'
    AND dt BETWEEN '%s' AND '%s' ORDER BY dt", min(eq$dt), max(eq$dt)))

curve <- bind_rows(
  eq |> transmute(dt, value = equity / first(equity), series = "strategy"),
  spy |> transmute(dt, value = adj_close / first(adj_close), series = "benchmark")
) |> mutate(dt = as.Date(dt))

final <- curve |> group_by(series) |> slice_tail(n = 1) |> ungroup()
p1 <- ggplot(curve, aes(dt, value, colour = series)) +
  geom_hline(yintercept = 1, colour = "grey80", linewidth = 0.3) +
  geom_line(linewidth = 0.7) +
  geom_text(data = final, aes(label = sprintf("%+.0f%%", (value - 1) * 100)),
            hjust = -0.1, size = 3.4, fontface = "bold", show.legend = FALSE) +
  scale_colour_manual(values = PAL,
                      labels = c(benchmark = "SPY buy and hold",
                                 strategy = "GBM strategy, net of costs")) +
  scale_y_continuous(labels = function(x) sprintf("%.1fx", x)) +
  scale_x_date(expand = expansion(mult = c(0.01, 0.08))) +
  labs(title = "The strategy did not beat buying the index",
       subtitle = sprintf("Growth of $1, out-of-sample only, %s to %s",
                          min(eq$dt), max(eq$dt)),
       x = NULL, y = "growth of $1",
       caption = paste("Strategy returns are net of modeled slippage, spread and",
                       "commission. Every prediction comes from a walk-forward fold\nthat",
                       "never saw the period it traded."))
save_fig(p1, "01_equity_vs_benchmark.png")

# ------------------------------------- 2  the Reality Check null distribution
rds <- file.path(ROOT, "reports", "validation.rds")
if (file.exists(rds)) {
  v <- readRDS(rds)
  d <- data.frame(x = v$boot_max * 100)
  p2 <- ggplot(d, aes(x)) +
    geom_histogram(bins = 60, fill = "grey75", colour = "white", linewidth = 0.2) +
    geom_vline(xintercept = v$obs_max * 100, colour = PAL[["benchmark"]],
               linewidth = 0.9) +
    annotate("label", x = v$obs_max * 100, y = Inf, vjust = 1.4,
             label = sprintf("observed best\n%+.3f%%  p = %.3f",
                             v$obs_max * 100, v$p_rc),
             size = 3.2, fill = "white", colour = PAL[["benchmark"]]) +
    labs(title = "The best of three strategies falls inside the range of chance",
         subtitle = paste("Stationary-bootstrap null distribution of the maximum",
                          "excess return across all strategies tested"),
         x = "best excess return per 5-day period (%)", y = "bootstrap replicates",
         caption = paste("White's Reality Check. The null imposes no edge on any",
                         "strategy, resamples in blocks to preserve the serial\ncorrelation",
                         "created by overlapping 5-day returns, and records the best",
                         "result. If the observed value sits inside\nthis distribution,",
                         "testing several ideas explains it."))
  save_fig(p2, "02_reality_check.png")
} else {
  cat("  skipping 02: run Rscript R/validate.R first\n")
}

# ------------------------------------------------- 3  per-fold excess return
fold <- dbGetQuery(con, "
  SELECT model_name, fold_id, test_start, mean_ret_net, trading_dates
  FROM v_fold_summary WHERE model_name IN ('logistic','gbm')")
base <- dbGetQuery(con, "
  SELECT p.fold_id, AVG(l.fwd_ret_5d_net) AS base_ret
  FROM predictions p JOIN labels l ON l.symbol=p.symbol AND l.dt=p.dt
  WHERE p.model_name='always_long' GROUP BY p.fold_id")

fd <- fold |>
  left_join(base, by = "fold_id") |>
  mutate(excess = (mean_ret_net - base_ret) * 100,
         year = substr(test_start, 1, 4),
         sign = ifelse(excess >= 0, "above baseline", "below baseline"))

p3 <- ggplot(fd, aes(year, excess, fill = sign)) +
  geom_hline(yintercept = 0, colour = "grey30", linewidth = 0.4) +
  geom_col(width = 0.68, position = position_dodge(0.72)) +
  facet_wrap(~model_name, ncol = 1) +
  scale_fill_manual(values = c("above baseline" = PAL[["strategy"]],
                               "below baseline" = PAL[["benchmark"]])) +
  labs(title = "The edge does not persist across regimes",
       subtitle = paste("Excess return over the always-long baseline, per",
                        "out-of-sample test year"),
       x = NULL, y = "excess return per 5-day period (%)",
       caption = paste("Each bar is one walk-forward fold. A strategy with a real",
                       "edge would clear the baseline consistently;\nalternating signs",
                       "are what noise looks like."))
save_fig(p3, "03_per_fold_excess.png", h = 6)

# -------------------------------------------------------- 4  cost waterfall
tr <- dbGetQuery(con, sprintf(
  "SELECT SUM(gross_pnl) g, SUM(costs) c, SUM(net_pnl) n, COUNT(*) k
   FROM backtest_trades WHERE bt_id = %d", bt))
wf <- data.frame(
  step = factor(c("gross P/L", "costs", "net P/L"),
                levels = c("gross P/L", "costs", "net P/L")),
  value = c(tr$g, -tr$c, tr$n),
  ymin = c(0, tr$n, 0),
  ymax = c(tr$g, tr$g, tr$n),
  kind = c("gross", "cost", "net"))

p4 <- ggplot(wf, aes(step, fill = kind)) +
  geom_rect(aes(xmin = as.numeric(step) - 0.34, xmax = as.numeric(step) + 0.34,
                ymin = ymin, ymax = ymax)) +
  geom_text(aes(y = pmax(ymax, ymin), label = sprintf("$%s", format(round(value), big.mark = ","))),
            vjust = -0.5, size = 3.6, fontface = "bold") +
  scale_fill_manual(values = c(gross = PAL[["neutral"]], cost = PAL[["benchmark"]],
                               net = PAL[["strategy"]]), guide = "none") +
  scale_y_continuous(labels = function(x) paste0("$", format(x, big.mark = ",")),
                     expand = expansion(mult = c(0.02, 0.12))) +
  labs(title = sprintf("Transaction costs consumed %.0f%% of the gross edge",
                       tr$c / tr$g * 100),
       subtitle = sprintf("%s round trips on a $10,000 account over seven years",
                          format(tr$k, big.mark = ",")),
       x = NULL, y = NULL,
       caption = paste("Costs are modeled: 5 bps slippage plus a 2 bps half-spread",
                       "per side, and per-share commission with a minimum.\nThe gross",
                       "edge was real. It was not large enough to survive being traded."))
save_fig(p4, "04_cost_waterfall.png", w = 7.5, h = 5)

# ------------------------------------------------- 5  permutation importance
imp_path <- file.path(ROOT, "reports", "feature_importance.csv")
if (file.exists(imp_path)) {
  imp <- read.csv(imp_path) |> arrange(desc(auc_drop)) |> head(14) |>
    mutate(feature = factor(feature, levels = rev(feature)),
           level = ifelse(grepl("^mkt_", feature), "market-wide", "stock-specific"))
  p5 <- ggplot(imp, aes(auc_drop, feature, fill = level)) +
    geom_col(width = 0.7) +
    geom_errorbar(aes(xmin = auc_drop - auc_drop_sd, xmax = auc_drop + auc_drop_sd),
                  orientation = "y", width = 0.25, colour = "grey30", linewidth = 0.3) +
    scale_fill_manual(values = c("market-wide" = PAL[["benchmark"]],
                                 "stock-specific" = PAL[["strategy"]])) +
    labs(title = "The model is timing the market, not picking stocks",
         subtitle = "Permutation importance, measured as AUC lost when a feature is shuffled",
         x = "AUC lost", y = NULL,
         caption = paste("The two dominant features are market-wide, identical across",
                         "every symbol on a given date. That is why the\nmodel's selections",
                         "cluster on a few dates, and why the effective sample size is a",
                         "fraction of the row count."))
  save_fig(p5, "05_feature_importance.png", h = 5.6)
} else {
  cat("  skipping 05: run python3 python/model.py first\n")
}

cat("done\n")
