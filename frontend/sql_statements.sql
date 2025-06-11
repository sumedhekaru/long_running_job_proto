CREATE TABLE fcst_app.items (
    item_nbr      INTEGER PRIMARY KEY,
    family        VARCHAR(64),
    class         INTEGER,
    perishable    INTEGER
);



CREATE TABLE fcst_app.sales (	
	item_nbr     INTEGER,
    wk_end_dt    DATE,
    unit_sales   FLOAT
);

-- Create an index on item_nbr for faster queries
CREATE INDEX idx_sales_item_nbr ON sales(item_nbr);


select * from fcst_app.items

select * from fcst_app.sales  where item_nbr = 2127114

--delete from fcst_app.sales where 1=1;

--drop table fcst_app.sales;

CREATE TABLE fcst_app.forecasts (
    item_nbr    INTEGER NOT NULL,
    wk_end_dt   DATE NOT NULL,
    forecast    FLOAT,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (item_nbr, wk_end_dt)
);

CREATE TABLE fcst_app.forecast_request (
    job_id              SERIAL PRIMARY KEY,
    user_id         VARCHAR(64),       -- Or INTEGER, depending on your user management
    submitted_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status          VARCHAR(256),
    num_items       INTEGER,            -- Number of items requested in the job
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

select * from fcst_app.forecast_request fr 

CREATE TABLE fcst_app.job_items (
    job_id      INTEGER NOT NULL,
    batch_id 	INTEGER not null,
    item_nbr    INTEGER NOT NULL,
    PRIMARY KEY (job_id, item_nbr),
    FOREIGN KEY (job_id) REFERENCES fcst_app.forecast_request(job_id)
    -- Optionally: FOREIGN KEY (item_nbr) REFERENCES fcst_app.items(item_nbr)
);


select * from fcst_app.job_items

--drop table fcst_app.job_items


-- Index for fast lookup by job
CREATE INDEX idx_job_items_job_id ON fcst_app.job_items(job_id);

