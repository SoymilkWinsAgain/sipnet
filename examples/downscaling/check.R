# Run from the repository root after building the Russell example for 2016.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1L) stop("Expected the directory containing year=2016.rds")
source("downscaling/prepare/yang.R")
paths <- list(sipnet_means = args[1])
means_year <- NA_integer_
matched <- data.table(index = 1L, Site_ID = "Russell")

clock <- seq(as.POSIXct("2016-01-01", tz = "UTC"), by = "3 hours", length.out = 366L * 8L)
annual <- readRDS(file.path(paths$sipnet_means, "year=2016.rds"))
stopifnot(identical(annual$site_ids, 1L), identical(attr(annual$time, "tzone"), "UTC"),
          identical(as.numeric(annual$time), as.numeric(clock)),
          identical(dim(annual$nee), c(1L, length(clock))),
          identical(dim(annual$le), dim(annual$nee)))

runs <- lapply(c("russell_1", "russell_2"), function(run) {
  flux <- read.table(file.path("tests/smoke", run, "sipnet.out"), header = TRUE)
  flux[flux$year == 2016L, ]
})
nee <- (runs[[1]]$nee + runs[[2]]$nee) / 2 / (10800 * 1000)
et <- (runs[[1]]$evapotranspiration + runs[[2]]$evapotranspiration) / 2
le <- et * 10 * 2499999.898751805 / 10800
stopifnot(isTRUE(all.equal(as.vector(annual$nee), nee, tolerance = 1e-12)),
          isTRUE(all.equal(as.vector(annual$le), le, tolerance = 1e-12)))

grid <- tempfile(fileext = ".parquet")
arrow::write_parquet(data.frame(index = 1L, utc = rev(annual$time)), grid)
rows <- read_grid(grid, 2016L)
time_id <- match(rows$utc, clock)
stopifnot(identical(sort(time_id), seq_along(clock)),
          all(rows$Site_ID == "Russell"),
          isTRUE(all.equal(rows$nee, nee[time_id] * 1e8 / 1.157407, tolerance = 1e-12)),
          isTRUE(all.equal(rows$le, le[time_id], tolerance = 1e-12)))
cat("Yang read both Russell fluxes at all", length(clock), "three-hour UTC starts.\n")
