> ## Documentation Index
> Fetch the complete documentation index at: https://docs.mem0.ai/llms.txt
> Use this file to discover all available pages before exploring further.

# Get Memories

> Get all memories.

The v2 get memories API is powerful and flexible, allowing for more precise memory listing without the need for a search query. It supports complex logical operations (AND, OR, NOT) and comparison operators for advanced filtering capabilities. The comparison operators include:

* `in`: Matches any of the values specified
* `gte`: Greater than or equal to
* `lte`: Less than or equal to
* `gt`: Greater than
* `lt`: Less than
* `ne`: Not equal to
* `icontains`: Case-insensitive containment check
* `*`: Wildcard character that matches everything

<CodeGroup>
  ```python Code theme={null}
  memories = client.get_all(
      filters={
          "AND": [
              {
                  "user_id": "alex"
              },
              {
                  "created_at": {"gte": "2024-07-01", "lte": "2024-07-31"}
              }
          ]
      }
  )
  ```

  ```python Output theme={null}
  {
      "results": [
          {
              "id": "f4cbdb08-7062-4f3e-8eb2-9f5c80dfe64c",
              "memory": "Alex is planning a trip to San Francisco from July 1st to July 10th",
              "created_at": "2024-07-01T12:00:00Z",
              "updated_at": "2024-07-01T12:00:00Z"
          },
          {
              "id": "a2b8c3d4-5e6f-7g8h-9i0j-1k2l3m4n5o6p",
              "memory": "Alex prefers vegetarian restaurants",
              "created_at": "2024-07-05T15:30:00Z",
              "updated_at": "2024-07-05T15:30:00Z"
          }
      ],
      "total": 2
  }
  ```
</CodeGroup>

## Graph Memory

To retrieve graph memory relationships between entities, pass `output_format="v1.1"` in your request. This will return memories with entity and relationship information from the knowledge graph.

<CodeGroup>
  ```python Code theme={null}
  memories = client.get_all(
      filters={
          "user_id": "alex"
      },
      output_format="v1.1"
  )
  ```

  ```python Output theme={null}
  {
      "results": [
          {
              "id": "f4cbdb08-7062-4f3e-8eb2-9f5c80dfe64c",
              "memory": "Alex is planning a trip to San Francisco",
              "entities": [
                  {
                      "id": "entity-1",
                      "name": "Alex",
                      "type": "person"
                  },
                  {
                      "id": "entity-2",
                      "name": "San Francisco",
                      "type": "location"
                  }
              ],
              "relations": [
                  {
                      "source": "entity-1",
                      "target": "entity-2",
                      "relationship": "traveling_to"
                  }
              ]
          }
      ]
  }
  ```
</CodeGroup>


## OpenAPI

````yaml post /v2/memories/
openapi: 3.0.1
info:
  title: Mem0 API Docs
  description: mem0.ai API Docs
  contact:
    email: deshraj@mem0.ai
  license:
    name: Apache 2.0
  version: v1
servers:
  - url: https://api.mem0.ai/
security:
  - ApiKeyAuth: []
paths:
  /v2/memories/:
    post:
      tags:
        - memories
      description: Get all memories.
      operationId: memories_list_v2
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/MemoryGetInputV2'
        required: true
      responses:
        '200':
          description: Successfully retrieved memories.
          content:
            application/json:
              schema:
                type: array
                items:
                  type: object
                  properties:
                    id:
                      type: string
                    memory:
                      type: string
                    created_at:
                      type: string
                      format: date-time
                    updated_at:
                      type: string
                      format: date-time
                    owner:
                      type: string
                    immutable:
                      description: Whether the memory is immutable.
                      title: Immutable
                      type: boolean
                      default: false
                    expiration_date:
                      type: string
                      format: date-time
                      description: >-
                        The date and time when the memory will expire. Format:
                        YYYY-MM-DD.
                      title: Expiration date
                      nullable: true
                      default: null
                    organization:
                      type: string
                    metadata:
                      type: object
                  required:
                    - id
                    - memory
                    - created_at
                    - updated_at
                    - total_memories
                    - owner
                    - organization
                    - type
        '400':
          description: Bad Request.
          content:
            application/json:
              schema:
                type: object
                properties:
                  message:
                    type: string
                    example: >-
                      One of the filters: app_id, user_id, agent_id, run_id is
                      required!
      x-code-samples:
        - lang: Python
          source: >-
            # To use the Python SDK, install the package:

            # pip install mem0ai


            from mem0 import MemoryClient

            client = MemoryClient(api_key="your_api_key", org_id="your_org_id",
            project_id="your_project_id")


            # Retrieve memories with filters

            memories = client.get_all(
                filters={
                    "AND": [
                        {
                            "user_id": "alex"
                        },
                        {
                            "created_at": {
                                "gte": "2024-07-01",
                                "lte": "2024-07-31"
                            }
                        }
                    ]
                },
                version="v2"
            )


            print(memories)
        - lang: JavaScript
          source: |-
            // To use the JavaScript SDK, install the package:
            // npm i mem0ai

            import MemoryClient from 'mem0ai';
            const client = new MemoryClient({ apiKey: "your-api-key" });

            const filters = {
              AND: [
                { user_id: 'alex' },
                { created_at: { gte: '2024-07-01', lte: '2024-07-31' } }
              ]
            };

            client.getAll({ filters, api_version: 'v2' })
              .then(result => console.log(result))
              .catch(error => console.error(error));
        - lang: cURL
          source: |-
            curl -X POST 'https://api.mem0.ai/v2/memories/' \
            -H 'Authorization: Token your-api-key' \
            -H 'Content-Type: application/json' \
            -d '{
              "filters": {
                "AND": [
                  { "user_id": "alex" },
                  { "created_at": { "gte": "2024-07-01", "lte": "2024-07-31" } }
                ]
              },
              "org_id": "your-org-id",
              "project_id": "your-project-id"
            }'
        - lang: Go
          source: "package main\n\nimport (\n\t\"bytes\"\n\t\"encoding/json\"\n\t\"fmt\"\n\t\"io/ioutil\"\n\t\"net/http\"\n)\n\nfunc main() {\n\turl := \"https://api.mem0.ai/v2/memories/\"\n\tfilters := map[string]interface{}{\n\t\t\"AND\": []map[string]interface{}{\n\t\t\t{\"user_id\": \"alex\"},\n\t\t\t{\"created_at\": map[string]string{\n\t\t\t\t\"gte\": \"2024-07-01\",\n\t\t\t\t\"lte\": \"2024-07-31\",\n\t\t\t}},\n\t\t},\n\t}\n\tpayload, _ := json.Marshal(map[string]interface{}{\"filters\": filters})\n\treq, _ := http.NewRequest(\"POST\", url, bytes.NewBuffer(payload))\n\treq.Header.Add(\"Authorization\", \"Token your-api-key\")\n\treq.Header.Add(\"Content-Type\", \"application/json\")\n\n\tres, _ := http.DefaultClient.Do(req)\n\tdefer res.Body.Close()\n\tbody, _ := ioutil.ReadAll(res.Body)\n\n\tfmt.Println(string(body))\n}"
        - lang: PHP
          source: |-
            <?php

            $curl = curl_init();

            $filters = [
              'AND' => [
                ['user_id' => 'alex'],
                ['created_at' => ['gte' => '2024-07-01', 'lte' => '2024-07-31']]
              ]
            ];

            curl_setopt_array($curl, [
              CURLOPT_URL => "https://api.mem0.ai/v2/memories/",
              CURLOPT_RETURNTRANSFER => true,
              CURLOPT_ENCODING => "",
              CURLOPT_MAXREDIRS => 10,
              CURLOPT_TIMEOUT => 30,
              CURLOPT_HTTP_VERSION => CURL_HTTP_VERSION_1_1,
              CURLOPT_CUSTOMREQUEST => "POST",
              CURLOPT_POSTFIELDS => json_encode(['filters' => $filters]),
              CURLOPT_HTTPHEADER => [
                "Authorization: Token your-api-key",
                "Content-Type: application/json"
              ],
            ]);

            $response = curl_exec($curl);
            $err = curl_error($curl);

            curl_close($curl);

            if ($err) {
              echo "cURL Error #:" . $err;
            } else {
              echo $response;
            }
        - lang: Java
          source: >-
            import com.konghq.unirest.http.HttpResponse;

            import com.konghq.unirest.http.Unirest;

            import org.json.JSONObject;


            JSONObject filters = new JSONObject()
                .put("AND", new JSONArray()
                    .put(new JSONObject().put("user_id", "alex"))
                    .put(new JSONObject().put("created_at", new JSONObject()
                        .put("gte", "2024-07-01")
                        .put("lte", "2024-07-31")
                    ))
                );

            HttpResponse<String> response =
            Unirest.post("https://api.mem0.ai/v2/memories/")
              .header("Authorization", "Token your-api-key")
              .header("Content-Type", "application/json")
              .body(new JSONObject().put("filters", filters).toString())
              .asString();

            System.out.println(response.getBody());
components:
  schemas:
    MemoryGetInputV2:
      type: object
      required:
        - filters
      properties:
        filters:
          title: Filters
          type: object
          description: >-
            A dictionary of filters to apply to retrieve memories. Available
            fields are: user_id, agent_id, app_id, run_id, created_at,
            updated_at, categories, keywords. Supports logical operators (AND,
            OR) and comparison operators (in, gte, lte, gt, lt, ne, contains,
            icontains, *). For categories field, use 'contains' for partial
            matching (e.g., {"categories": {"contains": "finance"}}) or 'in' for
            exact matching (e.g., {"categories": {"in":
            ["personal_information"]}}).
          properties:
            user_id:
              type: string
            agent_id:
              type: string
            app_id:
              type: string
            run_id:
              type: string
            created_at:
              type: string
              format: date-time
            updated_at:
              type: string
              format: date-time
            keywords:
              type: object
              properties:
                contains:
                  type: string
                icontains:
                  type: string
            categories:
              type: object
              properties:
                in:
                  type: array
                  items:
                    type: string
            metadata:
              type: object
          additionalProperties:
            type: object
            properties:
              in:
                type: array
              gte:
                type: string
              lte:
                type: string
              gt:
                type: string
              lt:
                type: string
              ne:
                type: string
              contains:
                type: string
              icontains:
                type: string
        fields:
          title: Fields
          type: array
          items:
            type: string
          description: >-
            A list of field names to include in the response. If not provided,
            all fields will be returned.
        page:
          title: Page
          type: integer
          default: 1
          description: 'Page number for pagination. Default: 1'
        page_size:
          title: Page Size
          type: integer
          default: 100
          description: 'Number of items per page. Default: 100'
        org_id:
          title: Organization id
          type: string
          nullable: true
          description: >-
            The unique identifier of the organization associated with the
            memory.
        project_id:
          title: Project id
          type: string
          nullable: true
          description: The unique identifier of the project associated with the memory.
  securitySchemes:
    ApiKeyAuth:
      type: apiKey
      in: header
      name: Authorization
      description: >-
        API key authentication. Prefix your Mem0 API key with 'Token '. Example:
        'Token your_api_key'

````

Built with [Mintlify](https://mintlify.com).