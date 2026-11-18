# Rscript annual_means.R MEMBERS.csv YEAR OUTPUT_DIRECTORY
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 3L) stop("Expected MEMBERS.csv YEAR OUTPUT_DIRECTORY")
manifest <- normalizePath(args[1], mustWork = TRUE)
year <- as.integer(args[2])
runs <- read.csv(manifest)
stopifnot(nrow(runs) > 0L, !anyNA(runs), !anyDuplicated(runs[c("site_id", "member_id")]))
sites <- sort(unique(runs$site_id))
members <- sort(unique(runs$member_id))
if (nrow(runs) != length(sites) * length(members)) stop("Missing site/member pair")
for (field in c("output", "climate")) {
  relative <- !startsWith(runs[[field]], "/")
  runs[[field]][relative] <- file.path(dirname(manifest), runs[[field]][relative])
}

# PEcAn's SIPNET converter uses get.lv() at its default 268.6465 K.
latent_heat <- 94.21 * (365 - (268.6465 - 273.15))^0.31249 * 4.183 * 1000
clock <- NULL
for (i in seq_along(sites)) {
  group <- runs[runs$site_id == sites[i], ]
  nee <- le <- vector("list", nrow(group))
  for (j in seq_len(nrow(group))) {
    path <- group$output[j]
    header <- grep("^\\s*year\\s+day\\s+time\\s+", readLines(path, n = 20L))
    if (length(header) != 1L) stop("Expected named SIPNET output columns in ", path)
    flux <- read.table(path, skip = header - 1L, header = TRUE)
    clim <- read.table(group$climate[j])[, 1:4]
    names(clim) <- c("year", "day", "hour", "duration_days")
    flux <- flux[flux$year == year, ]
    clim <- clim[clim$year == year, ]
    if (!nrow(flux) || nrow(flux) != nrow(clim) || any(clim$duration_days != 0.125) ||
        any(flux$day != clim$day) || any(abs(flux$time - clim$hour) > 0.0051)) {
      stop("Expected matching three-hour output and forcing for ", year, " in ", path)
    }
    hour <- clim$hour
    if (hour[1] %% 3 != 0 || any(hour < 0 | hour >= 24)) {
      stop("Expected forcing to start on a three-hour UTC boundary in ", path)
    }
    time <- as.POSIXct(sprintf("%d-01-01", year), tz = "UTC") +
            (flux$day - 1) * 86400 + floor(hour / 3) * 10800
    if (any(diff(as.numeric(time)) != 10800)) stop("Gap or overlap in ", path)
    if (is.null(clock)) {
      clock <- time
      result <- list(site_ids = sites, time = clock,
                     nee = matrix(NA_real_, length(sites), length(clock)),
                     le = matrix(NA_real_, length(sites), length(clock)))
    } else if (!identical(time, clock)) stop("Different member time axis in ", path)

    nee[[j]] <- flux$nee / 1000 / 10800
    le[[j]] <- flux$evapotranspiration * 10 * latent_heat / 10800
  }
  result$nee[i, ] <- rowMeans(do.call(cbind, nee), na.rm = TRUE)
  result$le[i, ] <- rowMeans(do.call(cbind, le), na.rm = TRUE)
}
dir.create(args[3], recursive = TRUE, showWarnings = FALSE)
path <- file.path(args[3], sprintf("year=%d.rds", year))
if (file.exists(path)) stop("Output already exists: ", path)
saveRDS(result, path)
cat(sprintf("%s: %d sites, %d members, %d three-hour starts\n",
            path, length(sites), length(members), length(clock)))
