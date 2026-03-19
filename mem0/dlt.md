> ## Documentation Index
> Fetch the complete documentation index at: https://docs.mem0.ai/llms.txt
> Use this file to discover all available pages before exploring further.

# Delete Memory

> Get or Update or delete a memory.



## OpenAPI

````yaml delete /v1/memories/{memory_id}/
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
  /v1/memories/{memory_id}/:
    delete:
      tags:
        - memories
      description: Get or Update or delete a memory.
      operationId: memories_delete
      parameters:
        - name: memory_id
          in: path
          required: true
          schema:
            type: string
            format: uuid
          description: The unique identifier of the memory to retrieve.
      responses:
        '204':
          description: Successful deletion of memory.
          content:
            application/json:
              schema:
                type: object
                properties:
                  message:
                    type: string
                    example: Memory deleted successfully!
      x-code-samples:
        - lang: Python
          source: >-
            # To use the Python SDK, install the package:

            # pip install mem0ai


            from mem0 import MemoryClient

            client = MemoryClient(api_key="your_api_key", org_id="your_org_id",
            project_id="your_project_id")


            memory_id = "<memory_id>"

            client.delete(memory_id=memory_id)
        - lang: JavaScript
          source: |-
            // To use the JavaScript SDK, install the package:
            // npm i mem0ai

            import MemoryClient from 'mem0ai';
            const client = new MemoryClient({ apiKey: "your-api-key" });

            // Delete a specific memory
            client.delete("<memory_id>")
              .then(result => console.log(result))
              .catch(error => console.error(error));
        - lang: cURL
          source: |-
            curl --request DELETE \
              --url https://api.mem0.ai/v1/memories/{memory_id}/ \
              --header 'Authorization: Token <api-key>'
        - lang: Go
          source: "package main\n\nimport (\n\t\"fmt\"\n\t\"net/http\"\n\t\"io/ioutil\"\n)\n\nfunc main() {\n\n\turl := \"https://api.mem0.ai/v1/memories/{memory_id}/\"\n\n\treq, _ := http.NewRequest(\"DELETE\", url, nil)\n\n\treq.Header.Add(\"Authorization\", \"Token <api-key>\")\n\n\tres, _ := http.DefaultClient.Do(req)\n\n\tdefer res.Body.Close()\n\tbody, _ := ioutil.ReadAll(res.Body)\n\n\tfmt.Println(res)\n\tfmt.Println(string(body))\n\n}"
        - lang: PHP
          source: |-
            <?php

            $curl = curl_init();

            curl_setopt_array($curl, [
              CURLOPT_URL => "https://api.mem0.ai/v1/memories/{memory_id}/",
              CURLOPT_RETURNTRANSFER => true,
              CURLOPT_ENCODING => "",
              CURLOPT_MAXREDIRS => 10,
              CURLOPT_TIMEOUT => 30,
              CURLOPT_HTTP_VERSION => CURL_HTTP_VERSION_1_1,
              CURLOPT_CUSTOMREQUEST => "DELETE",
              CURLOPT_HTTPHEADER => [
                "Authorization: Token <api-key>"
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
            Unirest.delete("https://api.mem0.ai/v1/memories/{memory_id}/")
              .header("Authorization", "Token <api-key>")
              .asString();
components:
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