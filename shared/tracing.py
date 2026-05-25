import os
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

def setup_tracing(app, service_name: str):
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    environment = os.environ.get("ENVIRONMENT", "local")

    if environment == "prod":
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
        exporter = CloudTraceSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(exporter))
        print(f"☁️ GCP Tracing enabled for {service_name}")
    else:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter(endpoint="http://jaeger-all-in-one:4317", insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        print(f"🔎 Jaeger Tracing enabled for {service_name}")

    FastAPIInstrumentor.instrument_app(app)