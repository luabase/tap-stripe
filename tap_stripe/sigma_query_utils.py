from genson import SchemaBuilder
import pandas as pd
import os
from datetime import datetime
import requests
import singer
from singer.transform import Transformer

# Logger for sigma queries
SIGMA_LOGGER = singer.get_logger()


def get_query_file_url(query_name: str, scheduled_query_runs: list):
    runs = [
        run
        for run in scheduled_query_runs
        if run.get("title") == query_name and run.get("status") == "completed"
    ]
    if len(runs) == 0:
        return None
    return runs[0].get("file").get("url")


def download_query_file(file_url: str, output_path: str, stripe_client_secret: str):
    # TODO: handle rate limiting and retries
    headers = {"Authorization": f"Bearer {stripe_client_secret}"}
    response = requests.get(file_url, headers=headers, timeout=300)
    with open(output_path, "w") as f:
        f.write(response.text)


def build_schema(file_dict: list):
    # TODO: handle nested fields
    schema_builder = SchemaBuilder()
    schema_builder.add_object(file_dict)
    schema = schema_builder.to_schema()
    return schema.get("items")


def sync_sigma_query(query_name: str, scheduled_query_runs: list, client_secret: str, folder_name: str = "stripe_files"):
    query_url = get_query_file_url(query_name, scheduled_query_runs)
    if not query_url:
        SIGMA_LOGGER.warning(
            f"No completed query run found for {query_name}, skipping..."
        )
        return

    # download the query file
    if not os.path.exists(folder_name):
        os.makedirs(folder_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stream_name = query_name.lower().replace(" ", "_").replace("-", "_")
    output_path = f"{folder_name}/{stream_name}_{timestamp}.csv"
    SIGMA_LOGGER.info(f"Downloading query file for {query_name} to {output_path}")
    download_query_file(query_url, output_path, client_secret)
    SIGMA_LOGGER.info(
        f"Finished downloading query file for {query_name} to {output_path}"
    )

    # convert the query file to a dictionary
    SIGMA_LOGGER.info(f"Converting query file {output_path} to dictionary")
    query_dict = pd.read_csv(output_path).to_dict(orient="records")

    # build the schema and write the schema to the target
    SIGMA_LOGGER.info(f"Building schema for {stream_name}")
    schema = build_schema(query_dict)

    # write the schema to the target
    singer.write_schema(
        stream_name, schema, key_properties=[]
    )  # no PK for sigma queries, we do full-append or drop and replace

    # write the records to the target
    for record in query_dict:
        with Transformer(singer.UNIX_SECONDS_INTEGER_DATETIME_PARSING) as transformer:
            rec = transformer.transform(record, schema)
            singer.write_record(stream_name, rec)

    SIGMA_LOGGER.info(f"Finished syncing {query_name}, deleting {output_path} and {folder_name}")
    os.remove(output_path)