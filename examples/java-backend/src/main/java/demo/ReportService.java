package demo;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.sqs.SqsClient;

/**
 * A tiny bank-style backend with two endpoints:
 *
 *   GET  /reports/{month}   read a month's loan report from S3
 *   POST /archive/{month}   copy it to the archive bucket, log the run in DynamoDB, notify a queue
 *
 * It uses the normal AWS SDK for Java. Nothing in it knows about asp: run it with
 * AWS_ENDPOINT_URL=http://localhost:5050 and `asp serve` records what it needs.
 */
public class ReportService {

    // The SDK reads the endpoint, region and keys from the environment.
    static final S3Client s3 = S3Client.builder().forcePathStyle(true).build();
    static final DynamoDbClient dynamo = DynamoDbClient.create();
    static final SqsClient sqs = SqsClient.create();

    public static void main(String[] args) throws IOException {
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8080"));
        HttpServer server = HttpServer.create(new InetSocketAddress(port), 0);
        server.createContext("/reports/", ReportService::report);
        server.createContext("/archive/", ReportService::archive);
        server.createContext("/health", http -> reply(http, 200, "ok\n"));
        server.start();
        System.out.println("report-service on http://localhost:" + port);
    }

    static void report(HttpExchange http) throws IOException {
        String month = lastPart(http);
        String csv = s3.getObjectAsBytes(b -> b.bucket("reports").key(month + ".csv")).asUtf8String();
        reply(http, 200, csv);
    }

    static void archive(HttpExchange http) throws IOException {
        String month = lastPart(http);
        s3.copyObject(b -> b.sourceBucket("reports").sourceKey(month + ".csv")
                            .destinationBucket("archive").destinationKey(month + ".csv"));
        dynamo.putItem(b -> b.tableName("jobs").item(Map.of(
                "id", AttributeValue.fromS("archive-" + month),
                "status", AttributeValue.fromS("done"))));
        String queue = sqs.getQueueUrl(b -> b.queueName("done")).queueUrl();
        sqs.sendMessage(b -> b.queueUrl(queue).messageBody(month + " archived"));
        reply(http, 200, "archived " + month + "\n");
    }

    static String lastPart(HttpExchange http) {
        String path = http.getRequestURI().getPath();
        return path.substring(path.lastIndexOf('/') + 1);
    }

    static void reply(HttpExchange http, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        http.sendResponseHeaders(status, bytes.length);
        http.getResponseBody().write(bytes);
        http.close();
    }
}
