interface InformesSchema {
    id: number;
    created_at: number;
    nombre: string;
    descripcion: string;
    referencia: string;
    tipo_informe: any | null;
    casos_id: number;
    data: any;
    images: any | null;
    url_images: any;
    final_report_text: string;
    final_report_file: any | null;
    update_at: number | null;
}
