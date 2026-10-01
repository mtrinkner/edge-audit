#!/usr/bin/env Rscript
# =============================================================================
# R/validate.R — does any of this survive a statistical test?
#
#   Rscript R/validate.R
#
# R owns this stage because block bootstrapping and multiple-comparison control
# are what it is genuinely better at than the alternatives, and because the
# conclusion of the project should be produced by the layer whose only job is to
# ask whether the result is real.
#
# THREE PROBLEMS THIS FILE EXISTS TO HANDLE
#
# 1. OVERLAPPING RETURNS. The label spans 5 trading days, so observations one day
#    apart share four days of outcome. A plain t-test treats them as independent
#    and overstates significance. The fix is a stationary bootstrap (Politis and
#    Romano 1994), which resamples blocks of random geometric length and so
#    preserves short-range dependence under the null.
#
# 2. FAT TAILS AND SKEW. Equity returns are not normal. The bootstrap makes no
#    distributional assumption, which is the point of using it over a t-test.
#
# 3. DATA SNOOPING. Four strategies were tested. The best of four looks good even
#    when none has an edge, so the maximum statistic needs its own null
#    distribution. White's Reality Check builds exactly that. Bonferroni and
#    Benjamini-Hochberg adjustments are reported alongside for comparison.
#
# The honest null is NOT "zero return". Equities drifted upward over the sample,
# so beating zero is unremarkable. The null that matters is "no better than the
# always_long baseline", and that is the comparison reported as the verdict.
# =============================================================================

suppressPackageStartupMessages({
  library(DBI); library(RSQLite); library(dplyr); library(tidyr); library(ggplot2)
})

set.seed(20260101)
# Resolve the project root whether this is run via Rscript from the project
# directory, from R/, or sourced interactively.
find_root <- function() {
  for (cand in c(".", "..", normalizePath(".", mustWork = FALSE))) {
    if (dir.exists(file.path(cand, "data")) &&
        file.exists(file.path(cand, "data", "warehouse.db"))) return(cand)
  }
  stop("cannot locate the project root; run this from the trading-bot directory")
}
ROOT <- find_root()
DB <- file.path(ROOT, "data", "warehouse.db")
FIG <- file.path(ROOT, "reports", "figures")
dir.create(FIG, recursive = TRUE, showWarnings = FALSE)

HORIZON <- 5L          # label horizon in trading days; sets the dependence scale
MEAN_BLOCK <- 10L      # bootstrap mean block length, deliberately > HORIZON
B <- 5000L             # bootstrap replicates
BASELINE <- "always_long"

cat(strrep("=", 76), "\n")
cat("STATISTICAL VALIDATION\n")
cat(strrep("=", 76), "\n")

con <- dbConnect(SQLite(), DB)
on.exit(dbDisconnect(con), add = TRUE)

daily <- dbGetQuery(con, "
  SELECT model_name, dt, n_selected, ret_net, ret_gross, selection
  FROM v_all_strategies_daily ORDER BY model_name, dt")

if (nrow(daily) == 0) stop("no strategy returns found; run python/model.py first")

# ---------------------------------------------------------------- panel setup
# Align every strategy on the same dates. Comparing a 1,754-date series against a
# 1,740-date one would let differing sample windows masquerade as differing skill.
wide <- daily |>
  select(model_name, dt, ret_net) |>
  pivot_wider(names_from = model_name, values_from = ret_net) |>
  arrange(dt) |>
  drop_na()

strategies <- setdiff(names(wide), "dt")
cat(sprintf("\n%d strategies on %d common dates (%s to %s)\n",
            length(strategies), nrow(wide), min(wide$dt), max(wide$dt)))
cat(sprintf("label horizon %d days, so roughly %.0f independent observations\n",
            HORIZON, nrow(wide) / HORIZON))

# --------------------------------------------------------- stationary bootstrap
# Resample indices in blocks whose lengths are geometric with mean MEAN_BLOCK.
# Wrapping at the end keeps the series stationary, which is what distinguishes
# this from a fixed-length moving-block bootstrap.
stationary_indices <- function(n, mean_block) {
  idx <- integer(0)
  p <- 1 / mean_block
  while (length(idx) < n) {
    start <- sample.int(n, 1L)
    len <- rgeom(1L, p) + 1L
    take <- ((start + seq_len(len) - 2L) %% n) + 1L
    idx <- c(idx, take)
  }
  idx[seq_len(n)]
}

boot_mean_ci <- function(x, B, mean_block, conf = 0.95) {
  n <- length(x)
  stats <- vapply(seq_len(B), function(i) mean(x[stationary_indices(n, mean_block)]),
                  numeric(1))
  a <- (1 - conf) / 2
  list(est = mean(x), se = sd(stats),
       lo = unname(quantile(stats, a)), hi = unname(quantile(stats, 1 - a)),
       # Two-sided p-value by inverting the bootstrap distribution, centered at
       # the null of zero mean.
       p = mean(abs(stats - mean(x)) >= abs(mean(x))))
}

# Paired difference against the baseline. Pairing by date removes the common
# market move, which is the whole reason to test against a baseline rather than
# against zero.
boot_diff_p <- function(x, y, B, mean_block) {
  d <- x - y
  n <- length(d)
  obs <- mean(d)
  centered <- d - obs                       # impose the null of no difference
  stats <- vapply(seq_len(B), function(i) mean(centered[stationary_indices(n, mean_block)]),
                  numeric(1))
  list(diff = obs, p = mean(abs(stats) >= abs(obs)),
       lo = unname(quantile(stats + obs, 0.025)),
       hi = unname(quantile(stats + obs, 0.975)))
}

cat("\n", strrep("-", 76), "\n", sep = "")
cat("TEST 1  mean 5-day net return, bootstrap CI and p-value (null: zero)\n")
cat(strrep("-", 76), "\n")
cat(sprintf("%-16s %10s %10s %20s %9s\n", "strategy", "mean %", "ann. %",
            "95% CI", "p"))

res1 <- lapply(strategies, function(s) {
  x <- wide[[s]]
  b <- boot_mean_ci(x, B, MEAN_BLOCK)
  periods <- 252 / HORIZON
  cat(sprintf("%-16s %10.4f %10.2f  [%7.4f, %7.4f] %9.4f\n",
              s, b$est * 100, ((1 + b$est)^periods - 1) * 100,
              b$lo * 100, b$hi * 100, b$p))
  data.frame(strategy = s, mean = b$est, lo = b$lo, hi = b$hi, p_vs_zero = b$p)
}) |> bind_rows()

cat("\n", strrep("-", 76), "\n", sep = "")
cat(sprintf("TEST 2  paired difference vs the %s baseline (the null that matters)\n",
            BASELINE))
cat(strrep("-", 76), "\n")
cat(sprintf("%-16s %12s %22s %9s\n", "strategy", "excess %", "95% CI", "p"))

contenders <- setdiff(strategies, BASELINE)
res2 <- lapply(contenders, function(s) {
  b <- boot_diff_p(wide[[s]], wide[[BASELINE]], B, MEAN_BLOCK)
  cat(sprintf("%-16s %12.4f  [%8.4f, %8.4f] %9.4f\n",
              s, b$diff * 100, b$lo * 100, b$hi * 100, b$p))
  data.frame(strategy = s, excess = b$diff, p_raw = b$p)
}) |> bind_rows()

# ------------------------------------------------- multiple-comparison control
cat("\n", strrep("-", 76), "\n", sep = "")
cat(sprintf("TEST 3  data-snooping adjustment across %d tested strategies\n",
            length(contenders)))
cat(strrep("-", 76), "\n")
res2$p_bonferroni <- pmin(1, res2$p_raw * nrow(res2))
res2$p_bh <- p.adjust(res2$p_raw, method = "BH")

# White's Reality Check: the null distribution of the BEST excess return across
# all strategies tested, not of each one separately. This is the test that
# answers "did I find something, or did I just try four things".
excess_mat <- sapply(contenders, function(s) wide[[s]] - wide[[BASELINE]])
obs_max <- max(colMeans(excess_mat))
n <- nrow(excess_mat)
centered <- sweep(excess_mat, 2, colMeans(excess_mat))   # impose the joint null
boot_max <- vapply(seq_len(B), function(i) {
  idx <- stationary_indices(n, MEAN_BLOCK)
  max(colMeans(centered[idx, , drop = FALSE]))
}, numeric(1))
p_rc <- mean(boot_max >= obs_max)

print(res2 |>
        mutate(across(c(excess), ~ sprintf("%.4f%%", .x * 100)),
               across(starts_with("p_"), ~ sprintf("%.4f", .x))),
      row.names = FALSE)
cat(sprintf("\nWhite's Reality Check: best excess = %.4f%% per 5 days, p = %.4f\n",
            obs_max * 100, p_rc))

# --------------------------------------------------------------------- verdict
cat("\n", strrep("=", 76), "\n", sep = "")
cat("VERDICT\n")
cat(strrep("=", 76), "\n")
alpha <- 0.05
survivors <- res2$strategy[res2$p_bh < alpha]
if (p_rc >= alpha) {
  cat(sprintf(paste0(
    "No strategy beats the %s baseline once data snooping is accounted for.\n",
    "The best of %d strategies produced %.4f%% excess return per 5-day period,\n",
    "and the Reality Check p-value is %.3f against a 0.05 threshold. With about\n",
    "%.0f independent observations and overlapping returns, a result this size is\n",
    "what testing %d ideas on one history of one market produces by chance.\n"),
    BASELINE, length(contenders), obs_max * 100, p_rc,
    nrow(wide) / HORIZON, length(contenders)))
} else {
  cat(sprintf("%s survives the Reality Check at p = %.4f.\n",
              paste(survivors, collapse = ", "), p_rc))
}
cat(sprintf("\nUnadjusted significance would have passed: %s\n",
            paste(res2$strategy[res2$p_raw < alpha], collapse = ", ") |>
              (\(x) if (nchar(x) == 0) "none" else x)()))
cat(sprintf("After Benjamini-Hochberg: %s\n",
            paste(survivors, collapse = ", ") |>
              (\(x) if (nchar(x) == 0) "none" else x)()))

# ----------------------------------------------------------------------- output
out <- res1 |> left_join(res2, by = "strategy")
write.csv(out, file.path(ROOT, "reports", "validation.csv"), row.names = FALSE)
write.csv(data.frame(reality_check_p = p_rc, best_excess = obs_max,
                     n_strategies = length(contenders), n_dates = nrow(wide),
                     n_effective = nrow(wide) / HORIZON,
                     bootstrap_reps = B, mean_block = MEAN_BLOCK),
          file.path(ROOT, "reports", "reality_check.csv"), row.names = FALSE)
cat("\nwrote reports/validation.csv and reports/reality_check.csv\n")

saveRDS(list(wide = wide, boot_max = boot_max, obs_max = obs_max, p_rc = p_rc),
        file.path(ROOT, "reports", "validation.rds"))
