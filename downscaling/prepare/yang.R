# Yang residual fitting and three-hour member routing.
suppressPackageStartupMessages(library(data.table))

features <- c("t2m","sp","d2m","tp","strd","KGC","lon","sin_doy","cos_doy",
  "NDVI","EVI","twi","PH","Sand","lat","agb","SOC","N","year_since_disturb","PPFD",
  "WindSpeed","t2m2","PPFD2","EVI2","VPD_t2m","sincos","sm_0_10","sm_10_40",
  "sm_40_100","sm_100_200","VPD","is_day")
members <- sprintf("ens%02d", 1:25)
groups <- c(as.character(1:8), "EC")

yang_matrix <- function(data, levels, ec) {
  columns <- c(features, if (ec) "LC")
  frame <- as.data.frame(data[, ..columns])
  for (field in intersect(names(levels), columns)) {
    values <- as.character(frame[[field]])
    if (any(!is.na(values) & !values %in% levels[[field]])) stop("Unknown ", field)
    frame[[field]] <- factor(values, levels = levels[[field]])
  }
  for (field in setdiff(columns, names(levels))) {
    if (is.logical(frame[[field]])) {
      if (any(!is.na(frame[[field]]))) stop("Unexpected logical predictor: ", field)
      frame[[field]] <- as.numeric(frame[[field]])
    }
  }
  formula <- reformulate(columns, intercept = FALSE)
  model.matrix(formula, model.frame(formula, frame, na.action = na.pass))
}

fit_group <- function(data, target, member, group, levels, threads) {
  raw <- data[[tolower(target)]]
  response <- raw - data[[member]]
  data <- data[is.finite(response)]
  setorder(data, index, utc)
  response <- data[[tolower(target)]] - data[[member]]
  ec <- group == "EC"
  if (nrow(data) < 10L) {
    if (ec) stop("Insufficient rows for EC model")
    return(list(model = NULL, levels = levels))
  }
  matrix <- yang_matrix(data, levels, ec)
  if (ec) {
    set.seed(2025L)
    training <- as.integer(caret::createDataPartition(response, p=.8, list=FALSE))
    validation <- setdiff(seq_along(response), training)
    train <- xgboost::xgb.DMatrix(matrix[training,,drop=FALSE], label=response[training])
    valid <- xgboost::xgb.DMatrix(matrix[validation,,drop=FALSE], label=response[validation])
    params <- list(objective="reg:squarederror", eta=.03, max_depth=12L, subsample=.85,
                   colsample_bytree=.95, min_child_weight=1, gamma=0, lambda=3, alpha=2, nthread=threads)
    model <- xgboost::xgb.train(params, train, nrounds=200, watchlist=list(train=train,eval=valid),
                                early_stopping_rounds=20, eval_metric="rmse", verbose=0)
  } else {
    set.seed(30000L + match(member, members))
    params <- list(objective="reg:squarederror", eta=.1, max_depth=6L, subsample=.8,
                   colsample_bytree=.8, min_child_weight=10, gamma=.5, lambda=3, alpha=2, nthread=threads)
    model <- xgboost::xgb.train(params, xgboost::xgb.DMatrix(matrix, label=response),
                                nrounds=200, verbose=0)
  }
  list(model=model, levels=levels, features=colnames(matrix))
}

read_grid <- function(path, year, ids=NULL) {
  ds <- arrow::open_dataset(path)
  if (!is.null(ids)) ds <- dplyr::filter(ds, index %in% ids)
  grid <- as.data.table(dplyr::collect(ds))
  grid[, utc := as.POSIXct(utc, tz="UTC")]
  if (!identical(means_year, year)) {
    means <<- readRDS(file.path(paths$sipnet_means, sprintf("year=%d.rds", year)))
    means_year <<- year
  }
  sites <- match(grid$index, means$site_ids)
  times <- match(as.numeric(grid$utc), as.numeric(means$time))
  if (anyNA(sites) || anyNA(times)) stop("SIPNET site or time unavailable: ", path)
  at <- cbind(sites, times)
  grid[, nee := means$nee[at] * 1e8 / 1.157407]
  grid[, le := means$le[at]]
  for (key in c("nee","le")) grid[!is.finite(get(key)) | abs(get(key)) > 1e6, (key) := NA_real_]
  grid[, Site_ID := matched$Site_ID[match(index, matched$index)]]
  grid
}

tower_data <- function(target, grid) {
  ids <- unique(na.omit(grid$Site_ID))
  if (!length(ids)) return(NULL)
  columns <- c("Site_ID", "utc", members)
  low <- min(grid$utc)
  high <- max(grid$utc)
  towers <- rbindlist(lapply(ids, function(site) {
    path <- file.path(paths$tower, paste0("target=", target), paste0("site=", site, ".parquet"))
    ds <- arrow::open_dataset(path)
    ds <- dplyr::filter(ds, utc >= low, utc <= high)
    as.data.table(dplyr::collect(dplyr::select(ds, dplyr::all_of(columns))))
  }))
  if (anyDuplicated(towers, by=c("Site_ID","utc"))) stop("Duplicate tower observation")
  towers
}

training_tables <- function() {
  for (i in seq_len(nrow(partitions))) {
    item <- partitions[i]
    ids <- matched[(index-1L) %/% 500L + 1L == item$chunk, index]
    if (!length(ids)) next
    grid <- read_grid(item$path, item$year, ids)
    for (target in c("NEE","LE")) {
      tower <- tower_data(target, grid)
      part <- tower[grid, on=.(Site_ID, utc)]
      raw <- tolower(target)
      columns <- unique(c("index","utc","LC",raw,features,members))
      part <- part[is.finite(get(raw)), ..columns]
      part <- part[rowSums(is.finite(as.matrix(part[, ..members]))) > 0]
      path <- file.path(work, "training", target, paste0(item$name, ".parquet"))
      dir.create(dirname(path), recursive=TRUE, showWarnings=FALSE)
      arrow::write_parquet(part, path)
    }
  }
  # Derive factor levels on the complete site domain.
  meta <- unique(rbindlist(lapply(partitions[year==min(year),path], function(path)
    unique(as.data.table(arrow::read_parquet(path, col_select=c("index","LC","KGC")))))))
  if (anyDuplicated(meta$index)) stop("Inconsistent site categories")
  levels <- lapply(setNames(groups, groups), function(g) {
    selected <- if (g=="EC") meta[index %in% matched$index] else if (as.integer(g)<=4) meta[LC<=4] else meta[LC>=5]
    list(KGC=sort(unique(na.omit(selected$KGC))), is_day=c("0","1"), LC=sort(unique(as.character(selected$LC))))
  })
  saveRDS(levels, file.path(work,"levels.rds"))
}

fit_member <- function(target, member) {
  levels <- readRDS(file.path(work,"levels.rds"))
  chunks <- unique((matched$index - 1L) %/% 500L + 1L)
  files <- file.path(work, "training", target, paste0(partitions[chunk %in% chunks,name], ".parquet"))
  columns <- unique(c("index","utc","LC",tolower(target),member,features))
  rows <- rbindlist(lapply(files, function(path) as.data.table(arrow::read_parquet(path,col_select=dplyr::all_of(columns)))))
  models <- lapply(setNames(groups, groups), function(g) {
    data <- if (g=="EC") rows else rows[LC==as.integer(g)]
    fit_group(data, target, member, g, levels[[g]], threads)
  })
  dir.create(file.path(work,"models"), showWarnings=FALSE)
  saveRDS(models, file.path(work,"models",paste0(target,"_",member,".rds")), compress=FALSE)
}

predict_partition <- function(i) {
  item <- partitions[i]
  panel <- read_grid(item$path, item$year)
  dest <- file.path(work, "members", item$name)
  dir.create(dest, recursive=TRUE, showWarnings=FALSE)
  for (target in c("NEE","LE")) {
    towers <- tower_data(target, panel)
    models <- lapply(members, function(member) {
      groups <- readRDS(file.path(work,"models",paste0(target,"_",member,".rds")))
      lapply(groups, function(bundle) {
        if (!is.null(bundle$model)) {
          bundle$model <- xgboost::xgb.Booster.complete(bundle$model)
          xgboost::xgb.parameters(bundle$model) <- list(nthread=threads)
        }
        bundle
      })
    })
    for (begin in seq.int(1L,nrow(panel),by=100000L)) {
      batch <- panel[begin:min(nrow(panel),begin+99999L)]
      direct <- if (!is.null(towers)) towers[batch[,.(Site_ID,utc)],on=.(Site_ID,utc)]
      group <- ifelse(!is.na(batch$Site_ID), "EC", as.character(batch$LC))
      raw <- batch[[tolower(target)]]
      values <- matrix(rep(raw,25), ncol=25)
      routes <- matrix(ifelse(is.finite(values),3L,0L),ncol=25)
      for (g in intersect(groups, group)) {
        ix <- which(group==g)
        matrix <- NULL
        for (j in seq_along(members)) {
          bundle <- models[[j]][[g]]
          if (is.null(bundle$model)) next
          if (is.null(matrix)) matrix <- yang_matrix(batch[ix],bundle$levels,g=="EC")
          if (!identical(colnames(matrix),bundle$features)) stop("Predictor order mismatch")
          residual <- predict(bundle$model,matrix)
          ok <- is.finite(raw[ix]) & is.finite(residual)
          values[ix[ok],j] <- raw[ix[ok]] - residual[ok]
          routes[ix[ok],j] <- 2L
        }
      }
      if (!is.null(direct)) {
        direct <- as.matrix(direct[, ..members])
        valid <- is.finite(direct)
        values[valid] <- direct[valid]
        routes[valid] <- 1L
      }
      out <- batch[,.(index,utc)]
      out[, raw := ..raw]
      out[, (members) := as.data.table(values)]
      out[, (sprintf("route%02d",1:25)) := as.data.table(routes)]
      arrow::write_parquet(out,file.path(dest,sprintf("%s_%07d.parquet",target,begin)))
    }
  }
}

main <- function(args) {
  config <- jsonlite::read_json(args[1], simplifyVector=TRUE)
  paths <<- config$paths
  work <<- config$work
  partitions <<- as.data.table(config$partitions)
  matched <<- fread(paths$matched_towers)[order(min_dist_m), .SD[1], by=index][min_dist_m<=1000]
  matched <<- matched[index %in% fread(paths$sites)$index]
  means_year <<- NA_integer_
  threads <<- as.integer(Sys.getenv("NSLOTS","1"))
  setDTthreads(threads)
  switch(args[2], training=training_tables(), fit=fit_member(args[3],args[4]),
         predict=predict_partition(as.integer(args[3])), stop("Unknown Yang stage"))
}
if (sys.nframe()==0L) main(commandArgs(TRUE))
