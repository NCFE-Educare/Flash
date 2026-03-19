> ## Documentation Index
> Fetch the complete documentation index at: https://docs.mem0.ai/llms.txt
> Use this file to discover all available pages before exploring further.

# Search Memories

> Search memories based on a query and filters.

The v2 search API is powerful and flexible, allowing for more precise memory retrieval. It supports complex logical operations (AND, OR, NOT) and comparison operators for advanced filtering capabilities. The comparison operators include:

* `in`: Matches any of the values specified
* `gte`: Greater than or equal to
* `lte`: Less than or equal to
* `gt`: Greater than
* `lt`: Less than
* `ne`: Not equal to
* `icontains`: Case-insensitive containment check
* `*`: Wildcard character that matches everything

<CodeGroup>
  ```python Platform API Example theme={null}
  related_memories = client.search(
      query="What are Alice's hobbies?",
      filters={
          "OR": [
              {
                "user_id": "alice"
              },
              {
                "agent_id": {"in": ["travel-agent", "sports-agent"]}
              }
          ]
      },
  )
  ```

  ```json Output theme={null}
  {
    "memories": [
      {
        "id": "ea925981-272f-40dd-b576-be64e4871429",
        "memory": "Likes to play cricket and plays cricket on weekends.",
        "metadata": {
          "category": "hobbies"
        },
        "score": 0.32116443111457704,
        "created_at": "2024-07-26T10:29:36.630547-07:00",
        "updated_at": null,
        "user_id": "alice",
        "agent_id": "sports-agent"
      }
    ],
  }
  ```
</CodeGroup>

<CodeGroup>
  ```python Wildcard Example theme={null}
  # Using wildcard to match all run_ids for a specific user
  all_memories = client.search(
      query="What are Alice's hobbies?",
      filters={
          "AND": [
              {
                  "user_id": "alice"
              },
              {
                  "run_id": "*"
              }
          ]
      },
  )
  ```
</CodeGroup>

<CodeGroup>
  ```python Categories Filter Examples theme={null}
  # Example 1: Using 'contains' for partial matching
  finance_memories = client.search(
      query="What are my financial goals?",
      filters={
          "AND": [
              { "user_id": "alice" },
              {
                  "categories": {
                      "contains": "finance"
                  }
              }
          ]
      },
  )

  # Example 2: Using 'in' for exact matching
  personal_memories = client.search(
      query="What personal information do you have?",
      filters={
          "AND": [
              { "user_id": "alice" },
              {
                  "categories": {
                      "in": ["personal_information"]
                  }
              }
          ]
      },
  )
  ```
</CodeGroup>


## OpenAPI

````yaml post /v2/memories/search/
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
  /v2/memories/search/:
    post:
      tags:
        - memories
      description: Search memories based on a query and filters.
      operationId: memories_search_v2
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/MemorySearchInputV2'
        required: true
      responses:
        '200':
          description: Successfully retrieved search results.
          content:
            application/json:
              schema:
                type: array
                items:
                  type: object
                  properties:
                    id:
                      type: string
                      format: uuid
                      description: Unique identifier for the memory.
                    memory:
                      type: string
                      description: The content of the memory
                    user_id:
                      type: string
                      description: The identifier of the user associated with this memory
                    metadata:
                      type: object
                      nullable: true
                      description: Additional metadata associated with the memory
                    categories:
                      type: array
                      items:
                        type: string
                      description: Categories associated with the memory
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
                    created_at:
                      type: string
                      format: date-time
                      description: The timestamp when the memory was created.
                    updated_at:
                      type: string
                      format: date-time
                      description: The timestamp when the memory was last updated.
                  required:
                    - id
                    - memory
                    - user_id
                    - created_at
                    - updated_at
      x-code-samples:
        - lang: Python
          source: >-
            # To use the Python SDK, install the package:

            # pip install mem0ai


            from mem0 import MemoryClient

            client = MemoryClient(api_key="your_api_key", org_id="your_org_id",
            project_id="your_project_id")


            query = "What do you know about me?"

            filters = {
               "OR":[
                  {
                     "user_id":"alex"
                  },
                  {
                     "agent_id":{
                        "in":[
                           "travel-assistant",
                           "customer-support"
                        ]
                     }
                  }
               ]
            }

            client.search(query, version="v2", filters=filters)
        - lang: JavaScript
          source: |-
            // To use the JavaScript SDK, install the package:
            // npm i mem0ai

            import MemoryClient from 'mem0ai';
            const client = new MemoryClient({ apiKey: "your-api-key" });

            const query = "What do you know about me?";
            const filters = {
              OR: [
                { user_id: "alex" },
                { agent_id: { in: ["travel-assistant", "customer-support"] } }
              ]
            };

            client.search(query, { api_version: "v2", filters })
              .then(result => console.log(result))
              .catch(error => console.error(error));
        - lang: cURL
          source: |-
            curl --request POST \
              --url https://api.mem0.ai/v2/memories/search/ \
              --header 'Authorization: Token <api-key>' \
              --header 'Content-Type: application/json' \
              --data '{
              "query": "<string>",
              "filters": {},
              "top_k": 123,
              "fields": [
                "<string>"
              ],
              "rerank": true,
              "org_id": "<string>",
              "project_id": "<string>"
            }'
        - lang: Go
          source: "package main\n\nimport (\n\t\"fmt\"\n\t\"strings\"\n\t\"net/http\"\n\t\"io/ioutil\"\n)\n\nfunc main() {\n\n\turl := \"https://api.mem0.ai/v2/memories/search/\"\n\n\tpayload := strings.NewReader(\"{\n  \\\"query\\\": \\\"<string>\\\",\n  \\\"filters\\\": {},\n  \\\"top_k\\\": 123,\n  \\\"fields\\\": [\n    \\\"<string>\\\"\n  ],\n  \\\"rerank\\\": true,\n  \\\"org_id\\\": \\\"<string>\\\",\n  \\\"project_id\\\": \\\"<string>\\\"\n}\")\n\n\treq, _ := http.NewRequest(\"POST\", url, payload)\n\n\treq.Header.Add(\"Authorization\", \"Token <api-key>\")\n\treq.Header.Add(\"Content-Type\", \"application/json\")\n\n\tres, _ := http.DefaultClient.Do(req)\n\n\tdefer res.Body.Close()\n\tbody, _ := ioutil.ReadAll(res.Body)\n\n\tfmt.Println(res)\n\tfmt.Println(string(body))\n\n}"
        - lang: PHP
          source: |-
            <?php

            $curl = curl_init();

            curl_setopt_array($curl, [
              CURLOPT_URL => "https://api.mem0.ai/v2/memories/search/",
              CURLOPT_RETURNTRANSFER => true,
              CURLOPT_ENCODING => "",
              CURLOPT_MAXREDIRS => 10,
              CURLOPT_TIMEOUT => 30,
              CURLOPT_HTTP_VERSION => CURL_HTTP_VERSION_1_1,
              CURLOPT_CUSTOMREQUEST => "POST",
              CURLOPT_POSTFIELDS => "{
              \"query\": \"<string>\",
              \"filters\": {},
              \"top_k\": 123,
              \"fields\": [
                \"<string>\"
              ],
              \"rerank\": true,
              \"org_id\": \"<string>\",
              \"project_id\": \"<string>\"
            }",
              CURLOPT_HTTPHEADER => [
                "Authorization: Token <api-key>",
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
            HttpResponse<String> response =
            Unirest.post("https://api.mem0.ai/v2/memories/search/")
              .header("Authorization", "Token <api-key>")
              .header("Content-Type", "application/json")
              .body("{
              \"query\": \"<string>\",
              \"filters\": {},
              \"top_k\": 123,
              \"fields\": [
                \"<string>\"
              ],
              \"rerank\": true,
              \"org_id\": \"<string>\",
              \"project_id\": \"<string>\"
            }")
              .asString();
components:
  schemas:
    MemorySearchInputV2:
      type: object
      required:
        - query
        - filters
      properties:
        query:
          title: Query
          type: string
          description: The query to search for in the memory.
        version:
          title: Version
          type: string
          default: v2
          description: The version of the memory to use. This should always be v2.
        filters:
          title: Filters
          type: object
          description: >-
            A dictionary of filters to apply to the search. Available fields
            are: user_id, agent_id, app_id, run_id, created_at, updated_at,
            categories, keywords. Supports logical operators (AND, OR) and
            comparison operators (in, gte, lte, gt, lt, ne, contains,
            icontains). For categories field, use 'contains' for partial
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
        top_k:
          title: Top K
          type: integer
          default: 10
          description: The number of top results to return.
        fields:
          title: Fields
          type: array
          items:
            type: string
          description: >-
            A list of field names to include in the response. If not provided,
            all fields will be returned.
        rerank:
          title: Rerank
          type: boolean
          default: false
          description: Whether to rerank the memories.
        keyword_search:
          title: Keyword search
          type: boolean
          default: false
          description: Whether to search for memories based on keywords.
        filter_memories:
          title: Filter memories
          type: boolean
          default: false
          description: Whether to filter the memories.
        threshold:
          title: Threshold
          type: number
          default: 0.3
          description: The minimum similarity threshold for returned results.
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